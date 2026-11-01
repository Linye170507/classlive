"""截止时间调度：单定时线程 + 最小堆，到点执行服务端权威收卷回调。

接口（docs/protocol.md 冻结）：
    schedule_at(ts_ms, callback, key=qid)
    cancel(qid)
"""

from __future__ import annotations

import heapq
import itertools
import logging
import threading
import time

log = logging.getLogger("classlive.timer")


class TimerScheduler:
    def __init__(self):
        self._cond = threading.Condition()
        self._heap: list[tuple[int, int, str, object]] = []
        self._cancelled: set[str] = set()
        self._seq = itertools.count()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._cond:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(target=self._loop, name="question-timer", daemon=True)
        self._thread.start()

    def schedule_at(self, ts_ms: int, callback, key: str = "") -> None:
        with self._cond:
            if key:
                self._cancelled.discard(key)
            heapq.heappush(self._heap, (int(ts_ms), next(self._seq), key, callback))
            self._cond.notify_all()

    def cancel(self, key: str) -> None:
        with self._cond:
            self._cancelled.add(key)
            self._cond.notify_all()

    def _loop(self) -> None:
        while True:
            with self._cond:
                while self._running:
                    now_ms = int(time.time() * 1000)
                    if not self._heap:
                        self._cond.wait(timeout=0.5)
                        continue
                    delay = self._heap[0][0] - now_ms
                    if delay <= 0:
                        break
                    self._cond.wait(timeout=min(delay / 1000.0, 0.5))
                if not self._running:
                    return

                ts, _, key, callback = heapq.heappop(self._heap)
                if key and key in self._cancelled:
                    self._cancelled.discard(key)
                    continue

            if ts <= int(time.time() * 1000):
                try:
                    callback()
                except Exception:
                    log.exception("定时回调执行失败 key=%s", key)

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
