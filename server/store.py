"""SQLite 持久化：唯一索兜底幂等，全部写操作由单一线程串行执行。

写线程模型（对应文档 2.3 / 4.2）：
- 主线程/连接线程只把 SQL 任务放进 queue；
- db-writer 线程独占 sqlite3.Connection，串行执行，彻底避免 database is locked；
- insert_submission 通过 Event 同步等待写结果，返回 True=首次写入、False=重复。
"""

from __future__ import annotations

import queue
import sqlite3
import threading
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rooms (
    room_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    state       TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS questions (
    room_id      TEXT NOT NULL,
    qid          TEXT NOT NULL,
    stem         TEXT NOT NULL,
    options_json TEXT NOT NULL,
    correct      TEXT NOT NULL,
    duration_s   INTEGER NOT NULL,
    state        TEXT NOT NULL,
    started_at   INTEGER,
    closed_at    INTEGER,
    PRIMARY KEY (room_id, qid)
);

CREATE TABLE IF NOT EXISTS submissions (
    room_id    TEXT NOT NULL,
    qid        TEXT NOT NULL,
    student_id TEXT NOT NULL,
    answer     TEXT NOT NULL,
    nonce      TEXT NOT NULL,
    submit_ts  INTEGER NOT NULL,
    seq        INTEGER NOT NULL,
    UNIQUE (room_id, qid, student_id)
);

CREATE INDEX IF NOT EXISTS idx_submissions_qid ON submissions (room_id, qid);

CREATE TABLE IF NOT EXISTS students (
    room_id    TEXT NOT NULL,
    student_id TEXT NOT NULL,
    nickname   TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen  INTEGER NOT NULL,
    PRIMARY KEY (room_id, student_id)
);
"""


class Store:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._init_error: BaseException | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._writer_loop, name="db-writer", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10.0):
            raise RuntimeError("SQLite 写线程初始化超时")
        if self._init_error is not None:
            raise RuntimeError(f"SQLite 初始化失败: {self._init_error}") from self._init_error

    def _writer_loop(self) -> None:
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA_SQL)
        except BaseException as exc:  # 初始化失败必须让主线程感知
            self._init_error = exc
            self._ready.set()
            if conn is not None:
                conn.close()
            return

        self._ready.set()
        try:
            while True:
                task = self._queue.get()
                if task is None:
                    self._queue.task_done()
                    break
                sql, params, fetch, event, box = task
                try:
                    cursor = conn.execute(sql, params)
                    box["result"] = cursor.fetchall() if fetch else cursor.rowcount
                    if not fetch:
                        conn.commit()
                except BaseException as exc:
                    box["error"] = exc
                finally:
                    event.set()
                    self._queue.task_done()
        finally:
            conn.close()

    def _run(self, sql: str, params: tuple, *, fetch: bool = False):
        if self._closed:
            raise RuntimeError("Store 已关闭")
        event = threading.Event()
        box: dict = {}
        self._queue.put((sql, params, fetch, event, box))
        if not event.wait(timeout=5.0):
            raise TimeoutError("SQLite 写任务超时")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def insert_submission(
        self,
        room_id: str,
        qid: str,
        student_id: str,
        answer: str,
        nonce: str,
        submit_ts: int,
        seq: int,
    ) -> bool:
        """True = 首次写入；False = 命中 UNIQUE(room_id,qid,student_id)，即重复提交。"""
        sql = (
            "INSERT OR IGNORE INTO submissions "
            "(room_id, qid, student_id, answer, nonce, submit_ts, seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        rowcount = self._run(sql, (room_id, qid, student_id, answer, nonce, submit_ts, seq))
        return rowcount > 0

    def count_submissions(self, room_id: str, qid: str) -> int:
        rows = self._run(
            "SELECT COUNT(*) FROM submissions WHERE room_id = ? AND qid = ?",
            (room_id, qid),
            fetch=True,
        )
        return int(rows[0][0])

    def get_submission(self, room_id: str, qid: str, student_id: str) -> dict | None:
        rows = self._run(
            "SELECT answer, submit_ts, seq FROM submissions "
            "WHERE room_id = ? AND qid = ? AND student_id = ?",
            (room_id, qid, student_id),
            fetch=True,
        )
        if not rows:
            return None
        answer, submit_ts, seq = rows[0]
        return {"answer": answer, "submit_ts": submit_ts, "seq": seq}

    def replayed_nonces(self, room_id: str, qid: str, nonce: str) -> int:
        rows = self._run(
            "SELECT COUNT(*) FROM submissions WHERE room_id = ? AND qid = ? AND nonce = ?",
            (room_id, qid, nonce),
            fetch=True,
        )
        return int(rows[0][0])

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join(timeout=5.0)
