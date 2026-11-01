"""统计聚合：纯内存、持锁计数；(qid, student_id) 是幂等的最小单位。"""

from __future__ import annotations

import threading

from protocol.errors import E_INVALID_ANSWER, ProtocolError

DEFAULT_OPTIONS = ("A", "B", "C", "D")


class StatsAggregator:
    def __init__(self, option_keys: tuple[str, ...] | list[str] = DEFAULT_OPTIONS):
        self._lock = threading.RLock()
        self._option_keys = tuple(option_keys) or DEFAULT_OPTIONS
        self._submitters: dict[str, set[str]] = {}
        self._counts: dict[str, dict[str, int]] = {}

    def _ensure(self, qid: str) -> None:
        if qid not in self._submitters:
            self._submitters[qid] = set()
            self._counts[qid] = {key: 0 for key in self._option_keys}

    def on_submit(self, qid: str, student_id: str, answer: str) -> tuple[bool, dict]:
        """返回 (是否首次计入, 当前计数快照)；同一学号同一题重复提交只计第一次。"""
        if answer not in self._option_keys:
            raise ProtocolError(E_INVALID_ANSWER)
        with self._lock:
            self._ensure(qid)
            if student_id in self._submitters[qid]:
                return False, dict(self._counts[qid])
            self._submitters[qid].add(student_id)
            self._counts[qid][answer] += 1
            return True, dict(self._counts[qid])

    def counts(self, qid: str) -> dict:
        with self._lock:
            self._ensure(qid)
            return dict(self._counts[qid])

    def has_submitted(self, qid: str, student_id: str) -> bool:
        with self._lock:
            return student_id in self._submitters.get(qid, set())

    def submitted_count(self, qid: str) -> int:
        with self._lock:
            return len(self._submitters.get(qid, set()))

    def snapshot(self, qid: str) -> dict:
        with self._lock:
            self._ensure(qid)
            counts = dict(self._counts[qid])
            return {
                "counts": counts,
                "total": sum(counts.values()),
                "submitted": len(self._submitters[qid]),
            }

    def reset(self, qid: str) -> None:
        with self._lock:
            self._submitters.pop(qid, None)
            self._counts.pop(qid, None)
