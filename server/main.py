"""ClassLive 服务端入口：accept 循环、每连接一线程、消息路由、心跳回收。

启动：
    python -m server.main
    python -m server.main --config config.json
"""

from __future__ import annotations

import argparse
import ctypes
import itertools
import json
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

from protocol import messages
from protocol.errors import (
    E_AUTH_FAILED,
    E_BAD_REQUEST,
    E_INVALID_ANSWER,
    E_ROLE_FORBIDDEN,
    E_ROOM_NOT_FOUND,
    ProtocolError,
)

from .connection import Connection
from .registry import CONN_DEAD, STATE_ANSWERED, STATE_ANSWERING, SessionRegistry
from .room import RoomManager
from .stats import StatsAggregator
from .store import Store
from .timer import TimerScheduler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config.json"

log = logging.getLogger("classlive.server")


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def _set_console_title(title: str) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


class ClassLiveServer:
    def __init__(self, config: dict):
        server_cfg = config["server"]
        timing = config.get("timing", {})
        room_cfg = config["room"]

        self.host = server_cfg.get("host", "127.0.0.1")
        self.port = int(server_cfg.get("port", 9000))
        self.backlog = int(server_cfg.get("backlog", 64))
        self.room_id = room_cfg["room_id"]
        self.heartbeat_interval_s = int(timing.get("heartbeat_interval_s", 10))
        self.scan_interval_s = float(timing.get("heartbeat_scan_interval_s", 1.0))

        self.room = RoomManager(self.room_id, room_cfg.get("title", self.room_id), config["questions"])
        self.stats = StatsAggregator(self.room.option_keys)
        self.registry = SessionRegistry(
            suspect_after_s=float(timing.get("suspect_after_s", 30)),
            dead_after_s=float(timing.get("dead_after_s", 60)),
        )
        db_path = Path(config.get("storage", {}).get("path", "data/classlive.db"))
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        self.store = Store(db_path)
        self.timer = TimerScheduler()

        self._running = False
        self._sock: socket.socket | None = None
        self._conn_threads: set[threading.Thread] = set()
        self._threads_lock = threading.Lock()
        self._shutdown_lock = threading.Lock()
        self._shutdown_done = False
        self._seq = itertools.count(1)
        self._last_qid = ""

    # ------------------------------------------------------------------ life cycle
    def serve_forever(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind((self.host, self.port))
            self._sock.listen(self.backlog)
        except OSError as exc:
            log.error("无法监听 %s:%s（端口可能被占用）: %s", self.host, self.port, exc)
            self._sock.close()
            self._sock = None
            raise
        self._sock.settimeout(1.0)

        self._running = True
        self.timer.start()
        threading.Thread(target=self._heartbeat_loop, name="heartbeat-monitor", daemon=True).start()
        log.info("ClassLive Server started, listening on %s", self.port)
        log.info("room=%s questions=%d sqlite=%s", self.room_id, len(self.room.all_questions()), self.store.db_path)

        try:
            while self._running:
                try:
                    client_sock, addr = self._sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                conn = Connection(client_sock, addr, self.room_id)
                log.info("TCP_ACCEPT addr=%s:%s", addr[0], addr[1])
                thread = threading.Thread(
                    target=self._connection_main,
                    args=(conn,),
                    name=f"conn-{addr[1]}",
                    daemon=True,
                )
                with self._threads_lock:
                    self._conn_threads.add(thread)
                thread.start()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """幂等关闭：即使从未成功启动（bind 失败），也要释放 Store 等资源。"""
        with self._shutdown_lock:
            if self._shutdown_done:
                return
            self._shutdown_done = True
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

        self.registry.close()
        self.timer.stop()
        with self._threads_lock:
            threads = list(self._conn_threads)
        for thread in threads:
            thread.join(timeout=1.0)
        self.store.close()
        log.info("ClassLive Server stopped")

    # ------------------------------------------------------------------ connections
    def _connection_main(self, conn: Connection) -> None:
        try:
            conn.recv_loop(self._on_message)
        finally:
            session = self.registry.remove_conn(conn)
            conn.close()
            with self._threads_lock:
                self._conn_threads.discard(threading.current_thread())
                active = len(self._conn_threads)
            if session is not None:
                log.info(
                    "LEAVE student_id=%s conn_threads=%d total_threads=%d",
                    session.student_id,
                    active,
                    threading.active_count(),
                )
                self._broadcast_stats()

    def _on_message(self, msg: dict, conn: Connection) -> None:
        ref_msg_id = msg.get("msg_id", "") if isinstance(msg, dict) else ""
        try:
            messages.validate(msg)
            if msg["room_id"] != self.room_id:
                raise ProtocolError(E_ROOM_NOT_FOUND)
            msg_type = msg["type"]

            session = self.registry.get_by_conn(conn)
            if session is not None:
                self.registry.touch(session.student_id)

            if msg_type == messages.JOIN:
                self._on_join(msg, conn)
            elif msg_type == messages.PING:
                self._on_ping(msg, conn)
            elif session is None:
                raise ProtocolError(E_AUTH_FAILED, "发送业务消息前必须先 JOIN")
            elif msg_type == messages.SUBMIT:
                self._on_submit(msg, session)
            elif msg_type == messages.RESYNC:
                self._on_resync(msg, session)
            elif msg_type == messages.Q_START:
                self._on_q_start(msg, session)
            elif msg_type == messages.Q_CLOSE:
                self._on_q_close(msg, session)
            else:
                raise ProtocolError(E_BAD_REQUEST, f"服务端不接收消息类型 {msg_type}")
        except ProtocolError as exc:
            log.info(
                "PROTOCOL_ERROR ref=%s type=%s student=%s code=%s msg=%s",
                ref_msg_id,
                msg.get("type", "?") if isinstance(msg, dict) else "?",
                getattr(conn, "student_id", None),
                exc.code,
                exc.msg,
            )
            conn.send(messages.make_error(exc.code, exc.msg, self.room_id, ref_msg_id))
        except Exception:
            log.exception("未处理异常 ref=%s", ref_msg_id)
            conn.send(messages.make_error(E_BAD_REQUEST, "服务端内部错误", self.room_id, ref_msg_id))

    # ------------------------------------------------------------------ handlers
    def _on_join(self, msg: dict, conn: Connection) -> None:
        student_id = msg["student_id"]
        token = msg["token"]
        role = msg["role"]
        nickname = msg["nickname"]

        existing = self.registry.get(student_id)
        if existing is not None:
            if existing.role != role:
                raise ProtocolError(E_AUTH_FAILED, "同一学号不能切换角色")

            old_conn = existing.conn
            if self.registry.token_matches(student_id, token):
                # 同号同 token：认领旧会话（session_id 不变，状态 RECONNECTED）。
                # 干净断开后会话在保留期内仍存在，因此这条路径对关窗口/进程被杀同样成立。
                if not self.registry.rebind(student_id, token, conn):
                    raise ProtocolError(E_AUTH_FAILED)
                if old_conn is not conn:
                    old_conn.close()
                session = self.registry.get(student_id)
                log.info(
                    "RECONNECT student_id=%s role=%s session=%s", student_id, role, session.session_id
                )
            elif existing.conn_state == CONN_DEAD:
                # 旧连接已确认断开、凭证已变（学生重启客户端拿到新 token）：允许接管重建
                log.info(
                    "TAKEOVER student_id=%s role=%s old_session=%s", student_id, role, existing.session_id
                )
                self.registry.bind(student_id, token, conn, nickname=nickname, role=role)
                session = self.registry.get(student_id)
                log.info("JOIN student_id=%s role=%s session=%s", student_id, role, session.session_id)
            else:
                raise ProtocolError(E_AUTH_FAILED, "会话凭证不匹配")
        else:
            self.registry.bind(student_id, token, conn, nickname=nickname, role=role)
            session = self.registry.get(student_id)
            log.info("JOIN student_id=%s role=%s nickname=%s session=%s", student_id, role, nickname, session.session_id)

        conn.student_id = student_id
        conn.session_id = session.session_id
        conn.role = role

        conn.send(
            messages.make(
                messages.WELCOME,
                self.room_id,
                session_id=session.session_id,
                server_ts=messages.now_ts(),
                heartbeat_s=self.heartbeat_interval_s,
                room_state=self.room.state,
            )
        )

        live = self.room.live_question()
        if live is not None:
            # 中途加入的老师/学生都要补发当前题目；老师端据此恢复题干与倒计时
            conn.send(self._q_started_payload(live))
            if role == messages.ROLE_STUDENT:
                self.registry.set_state(student_id, STATE_ANSWERING)

        self._broadcast_stats()

    def _on_ping(self, msg: dict, conn: Connection) -> None:
        # 活跃时间已由 _on_message 的 registry.touch 统一刷新，此处只回 PONG
        conn.send(messages.make(messages.PONG, self.room_id, server_ts=messages.now_ts()))

    def _on_submit(self, msg: dict, session) -> None:
        if session.role != messages.ROLE_STUDENT:
            raise ProtocolError(E_ROLE_FORBIDDEN, "只有学生可以提交答案")

        qid = msg["qid"]
        answer = msg["answer"]
        nonce = msg["nonce"]
        now_ms = messages.now_ts()

        # 判定与落库必须在同一把房间锁内完成：收卷在同一把锁上，二者串行，
        # 因此不会出现「已 CLOSED 但仍按陈旧 LIVE 接受提交」的窗口。
        with self.room.submit_window(qid, now_ms):
            if answer not in self.room.option_keys:
                raise ProtocolError(E_INVALID_ANSWER, f"answer={answer!r} 不在 A~D 范围内")

            seq = next(self._seq)
            inserted = self.store.insert_submission(
                self.room_id, qid, session.student_id, answer, nonce, now_ms, seq
            )
            if not inserted:
                record = self.store.get_submission(self.room_id, qid, session.student_id) or {}
                first_submit_ts = int(record.get("submit_ts", now_ms))
                session.conn.send(
                    messages.make(messages.SUBMIT_DUP, self.room_id, qid=qid, first_submit_ts=first_submit_ts)
                )
                log.info(
                    "SUBMIT_DUP qid=%s student=%s answer=%s nonce=%s", qid, session.student_id, answer, nonce
                )
                return

            accepted, counts = self.stats.on_submit(qid, session.student_id, answer)

        session.conn.send(
            messages.make(messages.SUBMIT_ACK, self.room_id, qid=qid, accepted=True, server_seq=seq)
        )
        self.registry.set_state(session.student_id, STATE_ANSWERED)
        log.info(
            "SUBMIT_ACK qid=%s student=%s answer=%s seq=%d counts=%s",
            qid,
            session.student_id,
            answer,
            seq,
            counts,
        )
        self._broadcast_stats()

    def _on_resync(self, msg: dict, session) -> None:
        snapshot = self.room.snapshot()
        qid = msg["qid"] or snapshot["qid"] or self._last_qid
        my_submission = (
            self.store.get_submission(self.room_id, qid, session.student_id) if qid else None
        )
        session.conn.send(
            messages.make(
                messages.RESYNC_STATE,
                self.room_id,
                room_state=self.room.state,
                qid=qid,
                deadline_ts=snapshot["deadline_ts"],
                my_submission=my_submission,
            )
        )
        log.info(
            "RESYNC student_id=%s qid=%s submitted=%s",
            session.student_id,
            qid or "-",
            my_submission is not None,
        )

    def _on_q_start(self, msg: dict, session) -> None:
        if session.role != messages.ROLE_TEACHER:
            raise ProtocolError(E_ROLE_FORBIDDEN, "只有老师可以发题")
        question = self.room.start_question(msg["qid"])
        self._last_qid = question.qid

        self._broadcast(self._q_started_payload(question))
        for student in self.registry.students():
            if student.conn_state != CONN_DEAD:
                self.registry.set_state(student.student_id, STATE_ANSWERING)

        self.timer.cancel(question.qid)
        self.timer.schedule_at(question.deadline_ts, lambda: self._on_deadline(question.qid), key=question.qid)
        log.info(
            "Q_START qid=%s deadline_ts=%s duration_s=%s students=%d",
            question.qid,
            question.deadline_ts,
            question.duration_s,
            self.registry.online_count(),
        )
        self._broadcast_stats()

    def _on_q_close(self, msg: dict, session) -> None:
        if session.role != messages.ROLE_TEACHER:
            raise ProtocolError(E_ROLE_FORBIDDEN, "只有老师可以收卷")
        question = self.room.close_question(msg["qid"])
        self.timer.cancel(question.qid)
        self._finish_question(question, reason="manual")

    def _on_deadline(self, qid: str) -> None:
        try:
            question = self.room.close_question(qid)
        except ProtocolError as exc:
            log.info("DEADLINE_IGNORED qid=%s: %s", qid, exc)
            return
        self._finish_question(question, reason="deadline")

    def _finish_question(self, question, reason: str) -> None:
        final_counts = self.stats.counts(question.qid)
        self._broadcast(
            messages.make(
                messages.Q_CLOSED,
                self.room_id,
                qid=question.qid,
                final_counts=final_counts,
                correct=question.correct,
            )
        )
        log.info(
            "Q_CLOSED qid=%s reason=%s final_counts=%s correct=%s",
            question.qid,
            reason,
            final_counts,
            question.correct,
        )
        self._broadcast_stats()

    # ------------------------------------------------------------------ broadcast
    def _q_started_payload(self, question) -> dict:
        return messages.make(
            messages.Q_STARTED,
            self.room_id,
            qid=question.qid,
            stem=question.stem,
            options=list(question.options),
            deadline_ts=question.deadline_ts,
            server_ts=messages.now_ts(),
        )

    def _broadcast(self, msg: dict, role: str | None = None) -> None:
        if role is None:
            sessions = self.registry.students() + self.registry.by_role(messages.ROLE_TEACHER)
        else:
            sessions = self.registry.by_role(role)
        for session in sessions:
            if session.conn_state == CONN_DEAD:
                continue
            try:
                session.conn.send(msg)
            except Exception:
                log.exception("广播失败 student_id=%s", session.student_id)

    def _broadcast_stats(self) -> None:
        qid = self.room.snapshot()["qid"] or self._last_qid
        if qid:
            data = self.stats.snapshot(qid)
        else:
            data = {
                "counts": {key: 0 for key in self.room.option_keys},
                "total": 0,
                "submitted": 0,
            }
        self._broadcast(
            messages.make(
                messages.STATS,
                self.room_id,
                qid=qid,
                counts=data["counts"],
                total=data["total"],
                submitted=data["submitted"],
                online=self.registry.online_count(),
            ),
            role=messages.ROLE_TEACHER,
        )

    # ------------------------------------------------------------------ heartbeat
    def _heartbeat_loop(self) -> None:
        while self._running:
            suspects, dead, expired = self.registry.scan()
            for session in suspects:
                log.info("SUSPECT student_id=%s idle>=%ss", session.student_id, self.registry.suspect_after_s)
            for session in dead:
                log.warning(
                    "DEAD student_id=%s idle>=%ss, closing（会话保留 %ss 供重连认领）",
                    session.student_id,
                    self.registry.dead_after_s,
                    self.registry.dead_after_s,
                )
                session.conn.close()
            for session in expired:
                log.info("PURGE student_id=%s 保留期结束，会话已移除", session.student_id)
            if dead:
                self._broadcast_stats()
            time.sleep(self.scan_interval_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ClassLive 课堂实时答题服务端")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    _set_console_title("ClassLive Server")

    config = load_config(args.config)
    server = ClassLiveServer(config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("收到 Ctrl+C，准备退出")
    except OSError:
        return 1
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
