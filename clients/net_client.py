"""客户端共用网络层：连接、收发线程、心跳、断线重连、会话认领。

约束：
- 绝不阻塞 UI 主线程：所有回调都在网络线程中触发，UI 必须只把事件塞进 queue；
- 重连退避 1s / 2s / 4s，最多 5 次；重连成功后自动重发 JOIN(token) 与 RESYNC。
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import uuid

from protocol import framing, messages
from protocol.errors import IncompleteFrame, ProtocolError

log = logging.getLogger("classlive.net")

RECV_CHUNK = 8192


class NetClient:
    def __init__(
        self,
        host: str,
        port: int,
        room_id: str,
        *,
        heartbeat_s: int = 10,
        auto_reconnect: bool = True,
        on_message=None,
        on_state=None,
        connect_timeout: float = 3.0,
    ):
        self.host = host
        self.port = int(port)
        self.room_id = room_id
        self.heartbeat_s = heartbeat_s
        self.auto_reconnect = auto_reconnect
        self.connect_timeout = connect_timeout

        self._message_callbacks: list = []
        self._state_callbacks: list = []
        if on_message is not None:
            self._message_callbacks.append(on_message)
        if on_state is not None:
            self._state_callbacks.append(on_state)

        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._connected = threading.Event()
        self._closed = threading.Event()
        self._recv_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._reconnect_thread: threading.Thread | None = None
        self._reconnecting = False
        self._identity: dict | None = None

    # ------------------------------------------------------------------ public API
    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def on_message(self, callback) -> None:
        self._message_callbacks.append(callback)

    def on_state(self, callback) -> None:
        self._state_callbacks.append(callback)

    def connect(self) -> bool:
        if self._closed.is_set():
            return False
        if self.is_connected:
            return True
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        except OSError as exc:
            log.warning("连接 %s:%s 失败: %s", self.host, self.port, exc)
            self._emit_state("connect_failed", str(exc))
            return False

        sock.settimeout(None)
        with self._lock:
            self._sock = sock
            self._connected.set()
        self._start_recv_thread(sock)
        self._emit_state("connected", f"{self.host}:{self.port}")
        return True

    def join(self, student_id: str, nickname: str, role: str, token: str | None = None) -> bool:
        """记录身份并发送 JOIN；重连时用同一 token 认领旧会话。"""
        self._identity = {
            "student_id": student_id,
            "nickname": nickname,
            "role": role,
            "token": token or uuid.uuid4().hex,
            "client_ver": "classlive-1.0",
        }
        ok = self.send(messages.make(messages.JOIN, self.room_id, **self._identity))
        if ok:
            self.start_heartbeat()
        return ok

    def send(self, msg: dict) -> bool:
        if not self.is_connected:
            return False
        try:
            data = framing.encode(msg)
        except ProtocolError as exc:
            log.warning("本地拒绝发送: %s", exc)
            return False
        with self._send_lock:
            sock = self._sock
            if sock is None:
                return False
            try:
                sock.sendall(data)
                return True
            except OSError as exc:
                log.warning("发送失败: %s", exc)
        self._handle_disconnect("send failed")
        return False

    def start_heartbeat(self) -> None:
        with self._lock:
            if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
                return
            self._heartbeat_stop.clear()
            thread = threading.Thread(target=self._heartbeat_loop, name="net-heartbeat", daemon=True)
            self._heartbeat_thread = thread
        thread.start()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._connected.clear()
        self._heartbeat_stop.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        self._emit_state("closed")

    # ------------------------------------------------------------------ internals
    def _start_recv_thread(self, sock: socket.socket) -> None:
        thread = threading.Thread(target=self._recv_loop, args=(sock,), name="net-recv", daemon=True)
        self._recv_thread = thread
        thread.start()

    def _recv_loop(self, sock: socket.socket) -> None:
        buf = b""
        try:
            while not self._closed.is_set():
                try:
                    chunk = sock.recv(RECV_CHUNK)
                except OSError as exc:
                    log.info("接收中断: %s", exc)
                    break
                if not chunk:
                    break
                buf += chunk
                while True:
                    try:
                        msg, buf = framing.decode(buf)
                    except IncompleteFrame:
                        break
                    except ProtocolError as exc:
                        log.warning("协议错误，断开重连: %s", exc)
                        self._emit_state("protocol_error", str(exc))
                        self._handle_disconnect("protocol error")
                        return
                    try:
                        messages.validate(msg)
                    except ProtocolError as exc:
                        log.warning("丢弃非法服务端消息: %s", exc)
                        continue
                    for callback in list(self._message_callbacks):
                        try:
                            callback(msg)
                        except Exception:
                            log.exception("消息回调异常")
        finally:
            with self._lock:
                current = self._sock is sock
            if current and not self._closed.is_set():
                self._handle_disconnect("recv loop ended")

    def _heartbeat_loop(self) -> None:
        while not self._closed.is_set():
            if self._heartbeat_stop.wait(self.heartbeat_s):
                return
            if not self.is_connected:
                continue
            self.send(messages.make(messages.PING, self.room_id))

    def _handle_disconnect(self, reason: str) -> None:
        start_reconnect = False
        with self._lock:
            if self._closed.is_set():
                return
            self._connected.clear()
            sock = self._sock
            self._sock = None
            if (
                self.auto_reconnect
                and self._identity is not None
                and not self._reconnecting
            ):
                self._reconnecting = True
                self._reconnect_thread = threading.Thread(
                    target=self._reconnect_loop, name="net-reconnect", daemon=True
                )
                start_reconnect = True
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self._emit_state("disconnected", reason)

        if start_reconnect and self._reconnect_thread is not None:
            self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        try:
            delays = [1, 2, 4, 4, 4]
            for attempt, delay in enumerate(delays, start=1):
                if self._closed.is_set():
                    return
                self._emit_state("reconnecting", f"第 {attempt}/5 次，{delay}s 后重试")
                if self._heartbeat_stop.wait(delay):
                    return
                if self.connect():
                    break
            else:
                self._emit_state("reconnect_failed", "5 次重连均失败")
                return

            if self._identity is None:
                return
            self.send(messages.make(messages.JOIN, self.room_id, **self._identity))
            self.send(messages.make(messages.RESYNC, self.room_id, qid=""))
            self.start_heartbeat()
            self._emit_state("reconnected", "会话已重新认领，已发送 RESYNC")
        finally:
            with self._lock:
                self._reconnecting = False

    def _emit_state(self, event: str, detail: str = "") -> None:
        for callback in list(self._state_callbacks):
            try:
                callback(event, detail)
            except Exception:
                log.exception("状态回调异常")
