"""Agent-facing paginated read of an accepted SQLite conversation."""
import pathlib
import sqlite3
import tempfile
import tracemalloc
import unittest

from session_search.corpus_store import CorpusPaths, ingest_artifact
from session_search.session_read import read_session
from tests.test_corpus_store import _message, _write_capture


class SessionReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.corpus = self.root / "corpus"
        path = _write_capture(
            self.root / "a.zip",
            "session-a",
            [
                _message("m1", "again", 1.0, role="user"),
                _message("m2", "again", 2.0, role="assistant"),
                _message("m3", "last", 3.0, role="user"),
            ],
            complete=False,
        )
        ingest_artifact(path, self.corpus)

    def test_pages_preserve_repeated_message_occurrences_and_partial_coverage(self):
        first = read_session(self.corpus, "session-a", limit=2)
        self.assertEqual(first["session_coverage"], "PARTIAL_SESSION_SLICE")
        self.assertIn("projection_stamp", first)
        self.assertNotIn("source_revision", first)
        self.assertEqual([(m["ordinal"], m["message_id"], m["text"]) for m in first["messages"]],
                         [(0, "m1", "again"), (1, "m2", "again")])
        self.assertTrue(first["has_more"])
        second = read_session(self.corpus, "session-a", cursor=first["next_cursor"], limit=2)
        self.assertEqual([(m["ordinal"], m["message_id"], m["text"]) for m in second["messages"]],
                         [(2, "m3", "last")])
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_cursor"])

    def test_start_at_ordinal_to_resume_around_search_hit(self):
        result = read_session(self.corpus, "session-a", start_ordinal=1)
        self.assertEqual([m["message_id"] for m in result["messages"]], ["m2", "m3"])

    def test_rejects_missing_session_and_invalid_limits(self):
        with self.assertRaisesRegex(ValueError, "SESSION_NOT_FOUND"):
            read_session(self.corpus, "not-a-session")
        with self.assertRaisesRegex(ValueError, "LIMIT_OUT_OF_BOUNDS"):
            read_session(self.corpus, "session-a", limit=0)

    def test_cursor_rejects_different_session_and_changed_projection(self):
        first = read_session(self.corpus, "session-a", limit=1)
        with self.assertRaisesRegex(ValueError, "CURSOR_SESSION_MISMATCH"):
            read_session(self.corpus, "other", cursor=first["next_cursor"])
        db = CorpusPaths.from_root(self.corpus).db
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE messages SET text='changed' WHERE message_id='m3'")
        with self.assertRaisesRegex(ValueError, "STALE_CURSOR"):
            read_session(self.corpus, "session-a", cursor=first["next_cursor"])

    def test_char_budget_also_bounds_python_materialization(self):
        huge_text = "x" * 2_000_000
        capture = _write_capture(
            self.root / "huge.zip",
            "session-huge",
            [_message("h1", huge_text, 4.0, role="assistant")],
            complete=True,
        )
        ingest_artifact(capture, self.corpus)
        # Exclude fixture construction/ingest from the measured read allocation.
        tracemalloc.start()
        try:
            result = read_session(self.corpus, "session-huge", limit=1, max_chars=32)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        message = result["messages"][0]
        self.assertEqual(message["text"], "x" * 32)
        self.assertEqual(message["text_total_chars"], 2_000_000)
        self.assertFalse(message["complete_message"])
        self.assertLess(peak, 500_000, f"Python allocated {peak:,} bytes for 32 characters")

    def test_oversized_message_yields_explicit_continuation(self):
        first = read_session(self.corpus, "session-a", limit=1, max_chars=3)
        self.assertEqual(first["messages"][0]["text"], "aga")
        self.assertEqual(first["messages"][0]["text_offset"], 0)
        self.assertEqual(first["messages"][0]["text_total_chars"], 5)
        self.assertFalse(first["messages"][0]["complete_message"])
        second = read_session(self.corpus, "session-a", cursor=first["next_cursor"],
                              limit=1, max_chars=3)
        self.assertEqual(second["messages"][0]["text"], "in")
        self.assertEqual(second["messages"][0]["text_offset"], 3)
        self.assertTrue(second["messages"][0]["complete_message"])
        third = read_session(self.corpus, "session-a", cursor=second["next_cursor"],
                             limit=1, max_chars=3)
        self.assertEqual(third["messages"][0]["message_id"], "m2")


if __name__ == "__main__":
    unittest.main()
