"""房间/题目状态机与角色权限的单元测试（B 验收：非法流转必须被拒）。"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from protocol import messages
from protocol.errors import (
    E_DEADLINE_PASSED,
    E_INVALID_ANSWER,
    E_Q_NOT_LIVE,
    E_ROLE_FORBIDDEN,
    E_UNKNOWN_QID,
    ProtocolError,
)
from server.main import ClassLiveServer

ROOM = "demo-room"


class RecordingConn:
    def __init__(self):
        self.sent: list[dict] = []
        self.student_id = None
        self.session_id = None
        self.role = None

    def send(self, msg: dict) -> bool:
        self.sent.append(msg)
        return True

    def close(self) -> None:
        pass


class StateMachineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        config = {
            "server": {"host": "127.0.0.1", "port": 0},
            "timing": {},
            "room": {"room_id": ROOM, "title": "状态机测试房间"},
            "storage": {"path": str(Path(self._tmp.name) / "test.db")},
            "questions": [
                {
                    "qid": "Q1",
                    "stem": "测试题 Q1",
                    "options": ["选项 A", "选项 B", "选项 C", "选项 D"],
                    "correct": "A",
                    "duration_s": 60,
                },
                {
                    "qid": "Q2",
                    "stem": "测试题 Q2",
                    "options": ["选项 A", "选项 B", "选项 C", "选项 D"],
                    "correct": "B",
                    "duration_s": 60,
                },
            ],
        }
        self.server = ClassLiveServer(config)
        self.conn = RecordingConn()
        self.teacher = SimpleNamespace(role=messages.ROLE_TEACHER, student_id="T01", conn=self.conn)
        self.student = SimpleNamespace(role=messages.ROLE_STUDENT, student_id="S001", conn=self.conn)

    def tearDown(self):
        self.server.shutdown()
        self._tmp.cleanup()

    # ------------------------------------------------------------------ helpers
    def _start(self, qid: str = "Q1"):
        self.server._on_q_start(self._make(messages.Q_START, qid=qid), self.teacher)

    def _close(self, qid: str = "Q1"):
        self.server._on_q_close(self._make(messages.Q_CLOSE, qid=qid), self.teacher)

    def _submit(self, qid: str = "Q1", answer: str = "A", session=None):
        self.server._on_submit(
            self._make(
                messages.SUBMIT,
                qid=qid,
                answer=answer,
                nonce="nonce-1",
                local_ts=messages.now_ts(),
            ),
            session or self.student,
        )

    @staticmethod
    def _make(msg_type: str, **fields) -> dict:
        return messages.make(msg_type, ROOM, **fields)

    def assert_code(self, code: int, func, *args) -> None:
        with self.assertRaises(ProtocolError) as ctx:
            func(*args)
        self.assertEqual(ctx.exception.code, code)

    # ------------------------------------------------------------------ 非法流转
    def test_submit_before_start_rejected(self):
        """未发题就提交 → 1004。"""
        self.assert_code(E_Q_NOT_LIVE, self._submit)

    def test_duplicate_start_rejected(self):
        """LIVE 状态重复 START → 1004。"""
        self._start("Q1")
        self.assert_code(E_Q_NOT_LIVE, self._start, "Q1")

    def test_second_question_while_live_rejected(self):
        """已有 LIVE 题时再发另一题 → 1004。"""
        self._start("Q1")
        self.assert_code(E_Q_NOT_LIVE, self._start, "Q2")

    def test_close_before_start_rejected(self):
        """DRAFT 状态直接收卷 → 1004。"""
        self.assert_code(E_Q_NOT_LIVE, self._close)

    def test_duplicate_close_rejected(self):
        """CLOSED 状态重复收卷 → 1004。"""
        self._start("Q1")
        self._close("Q1")
        self.assert_code(E_Q_NOT_LIVE, self._close, "Q1")

    def test_restart_closed_question_rejected(self):
        """已 CLOSED 的题不能再次 START → 1004。"""
        self._start("Q1")
        self._close("Q1")
        self.assert_code(E_Q_NOT_LIVE, self._start, "Q1")

    def test_submit_after_close_rejected(self):
        """已 CLOSED 再提交 → 1004。"""
        self._start("Q1")
        self._close("Q1")
        self.assert_code(E_Q_NOT_LIVE, self._submit)

    def test_unknown_qid_rejected(self):
        """发不存在的题 → 1006。"""
        self.assert_code(E_UNKNOWN_QID, self._start, "Q9")

    def test_invalid_answer_rejected(self):
        """选项越界 → 1007。"""
        self._start("Q1")
        self.assert_code(E_INVALID_ANSWER, self._submit, "Q1", "E")

    def test_deadline_passed_rejected(self):
        """超过服务端截止时间 → 1005。"""
        self._start("Q1")
        self.server.room.get("Q1").deadline_ts = messages.now_ts() - 1
        self.assert_code(E_DEADLINE_PASSED, self._submit)

    # ------------------------------------------------------------------ 角色权限
    def test_student_cannot_start_question(self):
        self.assert_code(E_ROLE_FORBIDDEN, self.server._on_q_start, self._make(messages.Q_START, qid="Q1"), self.student)

    def test_student_cannot_close_question(self):
        self.assert_code(E_ROLE_FORBIDDEN, self.server._on_q_close, self._make(messages.Q_CLOSE, qid="Q1"), self.student)

    def test_teacher_cannot_submit(self):
        self.assert_code(E_ROLE_FORBIDDEN, self._submit, "Q1", "A", self.teacher)

    # ------------------------------------------------------------------ 正常路径
    def test_valid_submit_then_duplicate(self):
        """首次提交 ACK + 计数 1；同人重复提交 DUP 且计数不变。"""
        self._start("Q1")
        self._submit("Q1", "A")
        self.assertEqual(self.conn.sent[-1]["type"], messages.SUBMIT_ACK)
        self.assertEqual(self.server.stats.snapshot("Q1")["counts"]["A"], 1)
        self.assertEqual(self.server.store.count_submissions(ROOM, "Q1"), 1)

        self._submit("Q1", "B")
        self.assertEqual(self.conn.sent[-1]["type"], messages.SUBMIT_DUP)
        self.assertEqual(self.server.stats.snapshot("Q1")["counts"]["A"], 1)
        self.assertEqual(self.server.stats.snapshot("Q1")["counts"]["B"], 0)
        self.assertEqual(self.server.store.count_submissions(ROOM, "Q1"), 1)

    def test_q_start_broadcasts_to_all_students(self):
        """发题广播：每个已 JOIN 的学生都收到 Q_STARTED。"""
        for student_id in ("S001", "S002"):
            conn = RecordingConn()
            self.server.registry.bind(student_id, f"token-{student_id}", conn, nickname=student_id)
        self._start("Q1")
        for student_id in ("S001", "S002"):
            session = self.server.registry.get(student_id)
            self.assertEqual(session.conn.sent[-1]["type"], messages.Q_STARTED)

    # ------------------------------------------------------------------ 并发竞态
    def test_concurrent_close_and_submit_never_accept_after_close(self):
        """收卷与提交并发：CLOSED 之后不得再有任何提交被记入（W1-1 竞态回归）。"""
        self._start("Q1")
        question = self.server.room.get("Q1")
        accepted: list[str] = []
        rejected: list[int] = []
        lock = threading.Lock()
        total = 20

        def worker(index: int) -> None:
            conn = RecordingConn()
            session = SimpleNamespace(
                role=messages.ROLE_STUDENT, student_id=f"S{index:03d}", conn=conn
            )
            try:
                self.server._on_submit(
                    self._make(
                        messages.SUBMIT,
                        qid="Q1",
                        answer="A",
                        nonce=f"nonce-{index}",
                        local_ts=messages.now_ts(),
                    ),
                    session,
                )
                last = conn.sent[-1]["type"] if conn.sent else ""
                with lock:
                    if last == messages.SUBMIT_ACK:
                        accepted.append(session.student_id)
            except ProtocolError as exc:
                with lock:
                    rejected.append(exc.code)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(total)]
        for thread in threads:
            thread.start()
        self._close("Q1")
        for thread in threads:
            thread.join(timeout=5)

        # 每一种结果都必须是合法语义：被接受（ACK）或被明确拒绝（1004/1005），不允许静默丢失
        self.assertEqual(len(accepted) + len(rejected), total)
        self.assertTrue(set(rejected) <= {E_Q_NOT_LIVE, E_DEADLINE_PASSED}, rejected)

        # 关键不变量：任一被接受的提交，落库时间都不晚于收卷时间
        for student_id in accepted:
            record = self.server.store.get_submission(ROOM, "Q1", student_id)
            self.assertIsNotNone(record, f"{student_id} 回了 ACK 却没有落库")
            self.assertLessEqual(record["submit_ts"], question.closed_at)

        # 内存计数与数据库必须一致（没有“ACK 但没入库”或“入库但没计数”）
        snapshot = self.server.stats.snapshot("Q1")
        self.assertEqual(snapshot["total"], len(accepted))
        self.assertEqual(self.server.store.count_submissions(ROOM, "Q1"), len(accepted))

    def test_room_returns_to_idle_when_draft_remains(self):
        """收卷后若仍有 DRAFT 题，房间回 IDLE（protocol.md 变更日志 R1）。"""
        self._start("Q1")
        self._close("Q1")
        self.assertEqual(self.server.room.state, "IDLE")
        self._start("Q2")
        self.assertEqual(self.server.room.state, "ACTIVE")


if __name__ == "__main__":
    unittest.main()
