"""房间/题目状态机（服务端权威）：所有时间与状态只在这里做判定。

房间：IDLE --Q_START--> ACTIVE --Q_CLOSE--> 无剩余题则 CLOSED，否则回 IDLE
题目：DRAFT --Q_START--> LIVE --Q_CLOSE/deadline--> CLOSED

说明：文档第 3.6 节的房间图只画了 IDLE→ACTIVE→CLOSED。演示脚本需要连续发两题，
因此收卷后若仍有 DRAFT 题，房间回到 IDLE 以便再次发题；这一条已记入 protocol.md 变更日志。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from protocol.errors import E_DEADLINE_PASSED, E_Q_NOT_LIVE, E_UNKNOWN_QID, ProtocolError

ROOM_IDLE = "IDLE"
ROOM_ACTIVE = "ACTIVE"
ROOM_CLOSED = "CLOSED"

Q_DRAFT = "DRAFT"
Q_LIVE = "LIVE"
Q_CLOSED = "CLOSED"


class Question:
    def __init__(self, qid: str, stem: str, options: list[str], correct: str, duration_s: int):
        self.qid = qid
        self.stem = stem
        self.options = options
        self.correct = correct
        self.duration_s = duration_s
        self.state = Q_DRAFT
        self.started_at: int | None = None
        self.deadline_ts: int | None = None
        self.closed_at: int | None = None

    def start(self) -> None:
        now_ms = int(time.time() * 1000)
        self.state = Q_LIVE
        self.started_at = now_ms
        self.deadline_ts = now_ms + self.duration_s * 1000

    def close(self) -> None:
        self.state = Q_CLOSED
        self.closed_at = int(time.time() * 1000)

    def public_payload(self) -> dict:
        return {
            "qid": self.qid,
            "stem": self.stem,
            "options": list(self.options),
            "deadline_ts": self.deadline_ts,
        }


class RoomManager:
    def __init__(self, room_id: str, title: str, questions: list[dict]):
        self.room_id = room_id
        self.title = title
        self.state = ROOM_IDLE
        self._lock = threading.RLock()
        self._questions: dict[str, Question] = {}
        for item in questions:
            qid = item["qid"]
            if qid in self._questions:
                raise ValueError(f"config.json 中存在重复 qid: {qid}")
            self._questions[qid] = Question(
                qid=qid,
                stem=item["stem"],
                options=list(item["options"]),
                correct=item["correct"],
                duration_s=int(item.get("duration_s", 60)),
            )

    @property
    def option_keys(self) -> tuple[str, ...]:
        first = next(iter(self._questions.values()), None)
        if first is None or not first.options:
            return ("A", "B", "C", "D")
        return tuple(chr(ord("A") + i) for i in range(len(first.options)))

    def get(self, qid: str) -> Question:
        question = self._questions.get(qid)
        if question is None:
            raise ProtocolError(E_UNKNOWN_QID)
        return question

    def start_question(self, qid: str) -> Question:
        with self._lock:
            question = self.get(qid)
            if question.state != Q_DRAFT:
                raise ProtocolError(E_Q_NOT_LIVE, f"题目 {qid} 当前状态为 {question.state}，不能重复发题")
            live = self.live_question()
            if live is not None:
                raise ProtocolError(E_Q_NOT_LIVE, f"题目 {live.qid} 仍在进行中，不能同时发题")
            question.start()
            self.state = ROOM_ACTIVE
            return question

    def close_question(self, qid: str) -> Question:
        with self._lock:
            question = self.get(qid)
            if question.state != Q_LIVE:
                raise ProtocolError(E_Q_NOT_LIVE, f"题目 {qid} 当前状态为 {question.state}，不能重复收卷")
            question.close()
            self.state = ROOM_IDLE if self._has_draft() else ROOM_CLOSED
            return question

    @contextmanager
    def submit_window(self, qid: str, now_ms: int):
        """持锁判定「题目 LIVE 且未过截止」，并把锁一直持有到 with 块结束。

        收卷 / 到点收卷与本方法竞争同一把锁，因此只有两种结果：
          1) 收卷先拿到锁 → 本题状态已 CLOSED，提交抛 1004；
          2) 提交先拿到锁 → 判定 + 落库 + 计数全部完成后才轮到收卷。
        不存在「题目已 CLOSED 却读到陈旧 LIVE、把提交记入并回 ACK」的窗口。
        """
        with self._lock:
            question = self.get(qid)
            if question.state != Q_LIVE:
                raise ProtocolError(E_Q_NOT_LIVE)
            if question.deadline_ts is not None and now_ms > question.deadline_ts:
                raise ProtocolError(E_DEADLINE_PASSED)
            yield question

    def live_question(self) -> Question | None:
        for question in self._questions.values():
            if question.state == Q_LIVE:
                return question
        return None

    def is_live(self, qid: str) -> bool:
        question = self._questions.get(qid)
        return question is not None and question.state == Q_LIVE

    def _has_draft(self) -> bool:
        return any(q.state == Q_DRAFT for q in self._questions.values())

    def snapshot(self) -> dict:
        with self._lock:
            live = self.live_question()
            return {
                "room_state": self.state,
                "qid": live.qid if live else "",
                "deadline_ts": live.deadline_ts if live else None,
            }

    def all_questions(self) -> list[Question]:
        with self._lock:
            return list(self._questions.values())
