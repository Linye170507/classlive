"""会话注册表与心跳扫描（A 验收）：干净断开后会话保留 60s，可被同 token 认领（W1-2）。"""

from __future__ import annotations

import time
import unittest

from protocol.errors import E_AUTH_FAILED, ProtocolError
from server.registry import (
    CONN_DEAD,
    CONN_OPEN,
    STATE_JOINED,
    STATE_RECONNECTED,
    SessionRegistry,
)


class FakeConn:
    """最简连接替身：只记录 student_id 与是否被关闭。"""

    def __init__(self, student_id: str | None = None):
        self.student_id = student_id
        self.session_id: str | None = None
        self.role: str | None = None
        self.closed = False

    def close(self) -> None:
        self.closed = True


class SessionRegistryTest(unittest.TestCase):
    def setUp(self):
        self.registry = SessionRegistry(suspect_after_s=30, dead_after_s=60)

    def tearDown(self):
        self.registry.close()

    def _join(self, student_id: str = "S001", token: str = "tok-1") -> tuple[str, FakeConn]:
        conn = FakeConn(student_id)
        session_id = self.registry.bind(student_id, token, conn, nickname="学生一")
        conn.session_id = session_id
        return session_id, conn

    def test_clean_disconnect_keeps_session_and_drops_online(self):
        """干净断开：会话仍在（DEAD），在线人数立即回落，session_id 不变。"""
        session_id, conn = self._join()
        self.assertEqual(self.registry.online_count(), 1)

        self.assertIsNotNone(self.registry.remove_conn(conn))
        session = self.registry.get("S001")
        self.assertIsNotNone(session, "干净断开后会话不应被立即删除")
        self.assertEqual(session.session_id, session_id)
        self.assertEqual(session.conn_state, CONN_DEAD)
        self.assertEqual(self.registry.online_count(), 0)

    def test_same_token_reclaims_session_id(self):
        """同号同 token 重新 JOIN：认领旧 session_id，状态 RECONNECTED。"""
        session_id, conn = self._join()
        self.registry.remove_conn(conn)

        new_conn = FakeConn("S001")
        new_session_id = self.registry.bind("S001", "tok-1", new_conn, nickname="学生一")

        self.assertEqual(new_session_id, session_id, "同 token 必须复用旧 session_id")
        session = self.registry.get("S001")
        self.assertEqual(session.state, STATE_RECONNECTED)
        self.assertEqual(session.conn_state, CONN_OPEN)
        self.assertIs(session.conn, new_conn)
        self.assertEqual(self.registry.online_count(), 1)

    def test_different_token_rejected_while_connection_alive(self):
        """旧连接仍存活时换 token → 1002，防止顶号。"""
        self._join()
        with self.assertRaises(ProtocolError) as ctx:
            self.registry.bind("S001", "tok-2", FakeConn("S001"), nickname="假冒")
        self.assertEqual(ctx.exception.code, E_AUTH_FAILED)

    def test_different_token_takes_over_after_dead(self):
        """旧连接已 DEAD（如学生重启进程拿到新 token）：允许接管，新 session_id。"""
        old_session_id, conn = self._join()
        self.registry.remove_conn(conn)

        new_session_id = self.registry.bind("S001", "tok-2", FakeConn("S001"), nickname="学生一")

        self.assertNotEqual(new_session_id, old_session_id)
        self.assertEqual(self.registry.get("S001").state, STATE_JOINED)
        self.assertEqual(self.registry.online_count(), 1)

    def test_dead_session_purged_after_retention(self):
        """DEAD 会话保留 60s；到期由 scan() 真正移除。"""
        session_id, conn = self._join()
        self.registry.remove_conn(conn)
        session = self.registry.get("S001")
        base = session.dead_since

        suspects, dead, expired = self.registry.scan(now=base + 59)
        self.assertEqual((suspects, dead, expired), ([], [], []))
        self.assertIsNotNone(self.registry.get("S001"))

        suspects, dead, expired = self.registry.scan(now=base + 61)
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].session_id, session_id)
        self.assertIsNone(self.registry.get("S001"))
        self.assertEqual(self.registry.online_count(), 0)

    def test_idle_session_becomes_dead_after_60s(self):
        """长时间无消息：先 SUSPECT（30s），再 DEAD（60s），DEAD 后不再计入在线。"""
        self._join()
        session = self.registry.get("S001")

        suspects, dead, expired = self.registry.scan(now=session.last_seen + 31)
        self.assertEqual(len(suspects), 1)
        self.assertEqual(session.conn_state, "SUSPECT")
        self.assertEqual(self.registry.online_count(), 1)

        suspects, dead, expired = self.registry.scan(now=session.last_seen + 61)
        self.assertEqual(len(dead), 1)
        self.assertEqual(session.conn_state, CONN_DEAD)
        self.assertIsNotNone(session.dead_since)
        self.assertEqual(self.registry.online_count(), 0)
        self.assertEqual(self.registry.get("S001"), session, "DEAD 会话仍保留，供重连认领")

    def test_touch_revives_suspect(self):
        """SUSPECT 状态下收到任何消息，刷新 last_seen 并回到 OPEN。"""
        self._join()
        session = self.registry.get("S001")
        self.registry.scan(now=time.monotonic() + 31)
        self.assertEqual(session.conn_state, "SUSPECT")

        self.registry.touch("S001")
        self.assertEqual(session.conn_state, CONN_OPEN)
        self.assertEqual(self.registry.online_count(), 1)


if __name__ == "__main__":
    unittest.main()
