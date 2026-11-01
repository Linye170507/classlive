"""单连接封装：发送加锁、收帧循环、协议错误回包。"""

from __future__ import annotations

import logging
import socket
import threading
import time

from protocol import framing, messages
from protocol.errors import E_FRAME_TOO_LARGE, ERROR_TEXT, ProtocolError

log = logging.getLogger("classlive.conn")

RECV_CHUNK = 8192


class Connection:
    """一条 TCP 连接的收发封装；所有 send 由同一把锁串行化，可跨线程调用。"""

    def __init__(self, sock: socket.socket, addr: tuple, room_id: str):
        self.sock = sock
        self.addr = addr
        self.room_id = room_id
        self._send_lock = threading.Lock()
        self._closed = False
        self.created_at = time.monotonic()
        self.last_recv_at = time.monotonic()
        self.session_id: str | None = None
        self.student_id: str | None = None
        self.role: str | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    def send(self, msg: dict) -> bool:
        """编码并发送一条消息；失败返回 False（连接已不可用）。"""
        if self._closed:
            return False
        try:
            data = framing.encode(msg)
        except ProtocolError as exc:
            log.warning("拒绝发送非法消息 addr=%s: %s", self.addr, exc)
            return False
        try:
            with self._send_lock:
                self.sock.sendall(data)
            return True
        except OSError as exc:
            log.info("发送失败 addr=%s: %s", self.addr, exc)
            self.close()
            return False

    def recv_loop(self, on_msg) -> None:
        """阻塞收帧，每解析出一帧调用 on_msg(msg, self)；协议错误回 ERROR 后断开。"""
        buf = b""
        while not self._closed:
            try:
                chunk = self.sock.recv(RECV_CHUNK)
            except OSError as exc:
                log.info("接收中断 addr=%s: %s", self.addr, exc)
                break
            if not chunk:
                log.info("对端关闭连接 addr=%s", self.addr)
                break

            self.last_recv_at = time.monotonic()
            buf += chunk

            while not self._closed:
                try:
                    msg, buf = framing.decode(buf)
                except framing.IncompleteFrame:
                    break
                except ProtocolError as exc:
                    log.warning("协议错误 addr=%s: %s", self.addr, exc)
                    self._send_protocol_error(exc)
                    if exc.code == E_FRAME_TOO_LARGE:
                        buf = b""
                    self.close()
                    return
                try:
                    on_msg(msg, self)
                except Exception:  # 业务异常不得拖垮收帧线程
                    log.exception("消息处理异常 addr=%s", self.addr)

    def _send_protocol_error(self, exc: ProtocolError) -> None:
        error = messages.make_error(
            exc.code,
            exc.msg or ERROR_TEXT.get(exc.code, "协议错误"),
            self.room_id,
            exc.ref_msg_id or "",
        )
        try:
            data = framing.encode(error)
            with self._send_lock:
                self.sock.sendall(data)
        except OSError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
