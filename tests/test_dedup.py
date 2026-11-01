"""B 的第 1 周验收：SQLite 重复插入返回 False、内存统计同人只计一次、并发计数精确。"""

import concurrent.futures
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.stats import StatsAggregator
from server.store import Store


class StoreDedupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.store = Store(self.db_path)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_duplicate_insert_returns_false(self):
        args = ("demo-room", "Q1", "S01", "A", "nonce-1", 1000, 1)
        self.assertTrue(self.store.insert_submission(*args))
        self.assertFalse(self.store.insert_submission(*args))
        self.assertEqual(self.store.count_submissions("demo-room", "Q1"), 1)

    def test_different_question_and_student_are_counted(self):
        self.assertTrue(self.store.insert_submission("demo-room", "Q1", "S01", "A", "n1", 1000, 1))
        self.assertTrue(self.store.insert_submission("demo-room", "Q2", "S01", "B", "n2", 1001, 2))
        self.assertTrue(self.store.insert_submission("demo-room", "Q1", "S02", "A", "n3", 1002, 3))
        self.assertEqual(self.store.count_submissions("demo-room", "Q1"), 2)
        self.assertEqual(self.store.count_submissions("demo-room", "Q2"), 1)

    def test_unique_index_exists_in_schema(self):
        conn = sqlite3.connect(self.db_path)
        try:
            indexes = conn.execute("PRAGMA index_list('submissions')").fetchall()
        finally:
            conn.close()
        unique_indexes = [row[1] for row in indexes if row[2] == 1]
        self.assertTrue(unique_indexes)


class StatsAggregatorTest(unittest.TestCase):
    def test_same_student_three_times_counts_once(self):
        stats = StatsAggregator()
        first, counts1 = stats.on_submit("Q1", "S01", "B")
        second, counts2 = stats.on_submit("Q1", "S01", "B")
        third, counts3 = stats.on_submit("Q1", "S01", "B")
        self.assertEqual([first, second, third], [True, False, False])
        self.assertEqual(counts1, counts2)
        self.assertEqual(counts2, counts3)
        self.assertEqual(stats.snapshot("Q1")["submitted"], 1)
        self.assertEqual(sum(stats.snapshot("Q1")["counts"].values()), 1)

    def test_same_student_different_questions_are_independent(self):
        stats = StatsAggregator()
        self.assertTrue(stats.on_submit("Q1", "S01", "A")[0])
        self.assertTrue(stats.on_submit("Q2", "S01", "C")[0])
        self.assertEqual(stats.snapshot("Q1")["submitted"], 1)
        self.assertEqual(stats.snapshot("Q2")["submitted"], 1)

    def test_fifty_threads_submit_exactly(self):
        stats = StatsAggregator()
        students = [f"S{i:02d}" for i in range(50)]

        def submit(student_id: str) -> bool:
            accepted, _ = stats.on_submit("Q1", student_id, "A")
            return accepted

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            results = list(pool.map(submit, students))
        self.assertEqual(results.count(True), 50)
        snapshot = stats.snapshot("Q1")
        self.assertEqual(snapshot["submitted"], 50)
        self.assertEqual(snapshot["counts"]["A"], 50)


if __name__ == "__main__":
    unittest.main()
