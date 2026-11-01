"""会话注册表：student_id ↔ 连接，token 认领，在线计数，心跳状态。

连接状态机（每条 socket）：
    OPEN ──(30s 无消息)──> SUSPECT ──(60s 无消息)──> DEAD

注意：DEAD 是「连接」状态而非「会话」生命周期。会话在 DEAD 后仍保留 dead_after_s，
供同号同 token 重连认领（干净断开也成立），到期由 scan() 真正移除。

学生状态机（会话维度）：
    JOINED ──(Q_STARTED)──> ANSWERING ──(SUBMIT_ACK)──> ANSWERED
    ANSWERED/在线 ──(重连认领)──> RECONNECTED
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from protocol.errors import E_AUTH_FAILED, ProtocolError

STATE_JOINED = "JOINED"
STATE_ANSWERING = "ANSWERING"
STATE_ANSWERED = "ANSWERED"
STATE_RECONNECTED = "RECONNECTED"

CONN_OPEN = "OPEN"
CONN_SUSPECT = "SUSPECT"
CONN_DEAD = "DEAD"


@dataclass
class Session:
    session_id: str
    student_id: str
    token: str
    nickname: str
    role: str
    conn: object
    state: str = STATE_JOINED
    conn_state: str = CONN_OPEN
    joined_ts: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.monotonic)
    dead_since: float | None = None

    def display_name(self) -> str:
        return self.nickname or self.student_id


class SessionRegistry:
    def __init__(self, suspect_after_s: float = 30.0, dead_after_s: float = 60.0):
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}
        self.suspect_after_s = suspect_after_s
        self.dead_after_s = dead_after_s

    def bind(
        self,
        student_id: str,
        token: str,
        conn,
        *,
        nickname: str = "",
        role: str = "student",
    ) -> str:
        """新建会话或认领旧会话。

        - 同号 + 同 token：认领旧会话，`session_id` 不变，状态置 RECONNECTED；
        - 同号 + 不同 token：仅当旧会话已 DEAD（连接已确认断开，如学生重启进程）时
          允许以新凭证重建会话；旧会话仍 OPEN/SUSPECT 时视为凭证冲突，抛 E_AUTH_FAILED。
        """
        with self._lock:
            existing = self._sessions.get(student_id)
            if existing is not None:
                if existing.token == token:
                    self._adopt(existing, conn, nickname, role, reconnect=True)
                    return existing.session_id
                if existing.conn_state != CONN_DEAD:
                    raise ProtocolError(E_AUTH_FAILED)

            session = Session(
                session_id=uuid.uuid4().hex,
                student_id=student_id,
                token=token,
                nickname=nickname,
                role=role,
                conn=conn,
            )
            self._sessions[student_id] = session
            return session.session_id

    def rebind(self, student_id: str, token: str, conn) -> bool:
        """重连认领：同号且 token 一致返回 True，否则 False（不新建会话）。"""
        with self._lock:
            session = self._sessions.get(student_id)
            if session is None or session.token != token:
                return False
            self._adopt(session, conn, session.nickname, session.role, reconnect=True)
            return True

    def _adopt(self, session: Session, conn, nickname: str, role: str, reconnect: bool = False) -> None:
        session.conn = conn
        session.nickname = nickname or session.nickname
        session.role = role or session.role
        session.state = STATE_RECONNECTED if reconnect else STATE_JOINED
        session.conn_state = CONN_OPEN
        session.last_seen = time.monotonic()
        session.dead_since = None

    def get(self, student_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(student_id)

    def get_by_conn(self, conn) -> Session | None:
        with self._lock:
            session = self._sessions.get(getattr(conn, "student_id", None))
            if session is not None and session.conn is conn:
                return session
            return None

    def has(self, student_id: str) -> bool:
        with self._lock:
            return student_id in self._sessions

    def token_matches(self, student_id: str, token: str) -> bool:
        with self._lock:
            session = self._sessions.get(student_id)
            return session is not None and session.token == token

    def touch(self, student_id: str) -> None:
        with self._lock:
            session = self._sessions.get(student_id)
            if session is not None:
                session.last_seen = time.monotonic()
                if session.conn_state == CONN_SUSPECT:
                    session.conn_state = CONN_OPEN

    def set_state(self, student_id: str, state: str) -> None:
        with self._lock:
            session = self._sessions.get(student_id)
            if session is not None:
                session.state = state

    def online_count(self, role: str | None = "student") -> int:
        with self._lock:
            return sum(
                1
                for s in self._sessions.values()
                if s.conn_state != CONN_DEAD and (role is None or s.role == role)
            )

    def students(self) -> list[Session]:
        with self._lock:
            return [s for s in self._sessions.values() if s.role == "student"]

    def by_role(self, role: str) -> list[Session]:
        with self._lock:
            return [s for s in self._sessions.values() if s.role == role]

    def remove_conn(self, conn) -> Session | None:
        """连接断开时调用：只标记 DEAD 并保留会话，由 scan() 在保留期后真正移除。

        保留期（dead_after_s）内同号同 token 的 JOIN 可以认领旧 session_id，
        这正是协议 §7「重连认领」在干净断开（关窗口 / 进程被杀）下也能成立的前提。
        返回被标记的会话；若该连接已不是会话的当前连接（已被新连接认领），返回 None。
        """
        with self._lock:
            student_id = getattr(conn, "student_id", None)
            session = self._sessions.get(student_id)
            if session is None or session.conn is not conn:
                return None
            if session.conn_state != CONN_DEAD:
                session.conn_state = CONN_DEAD
                session.dead_since = time.monotonic()
            return session

    def scan(self, now: float | None = None) -> tuple[list[Session], list[Session], list[Session]]:
        """扫描心跳，返回 (新进入 SUSPECT, 新进入 DEAD, 保留期到期被移除的会话)。

        DEAD 会话不会立刻删除：在 dead_after_s 保留期内留在注册表里供重连认领，
        到期后才真正移除；`online_count` 从一开始就不计入 DEAD，人数照常立即回落。
        """
        now = time.monotonic() if now is None else now
        suspects: list[Session] = []
        dead: list[Session] = []
        expired: list[Session] = []
        with self._lock:
            for student_id, session in list(self._sessions.items()):
                if session.conn_state == CONN_DEAD:
                    since = session.dead_since if session.dead_since is not None else now
                    if now - since >= self.dead_after_s:
                        del self._sessions[student_id]
                        expired.append(session)
                    continue
                idle = now - session.last_seen
                if idle >= self.dead_after_s:
                    session.conn_state = CONN_DEAD
                    session.dead_since = now
                    dead.append(session)
                elif idle >= self.suspect_after_s and session.conn_state == CONN_OPEN:
                    session.conn_state = CONN_SUSPECT
                    suspects.append(session)
        return suspects, dead, expired

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            conn = session.conn
            close = getattr(conn, "close", None)
            if callable(close):
                close()
