"""Agent-facing paginated read of an accepted SQLite conversation."""
import base64
import json
import pathlib
import sqlite3
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

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
        self.assertEqual(message["text_total_bytes"], 2_000_000)
        self.assertFalse(message["complete_message"])
        self.assertLess(peak, 500_000, f"Python allocated {peak:,} bytes for 32 characters")

    def test_nul_and_multibyte_characters_survive_each_page(self):
        original = "é\x00🍵after"
        capture = _write_capture(
            self.root / "nul.zip",
            "session-nul",
            [_message("nul1", original, 5.0)],
            complete=True,
        )
        ingest_artifact(capture, self.corpus)
        segments = []
        cursor = None
        for _ in range(len(original) + 2):
            page = read_session(self.corpus, "session-nul", limit=1, max_chars=2, cursor=cursor)
            item = page["messages"][0]
            segments.append(item["text"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        else:
            self.fail("reader did not reach end of NUL-containing message")
        self.assertEqual("".join(segments), original)
        self.assertEqual(item["text_total_bytes"], len(original.encode("utf-8")))
        self.assertTrue(item["complete_message"])

    def test_reconstructs_long_partial_session_across_many_pages_and_resumes(self):
        contents = [
            "", "repeated", "repeated", "🍵", "NUL\x00suffix", "é🍵" * 12,
            "long" * 21, "after", "repeated", "", "🏄\x00ä", "last",
        ]
        contents.extend(f"turn {n}: " + ("β" * (n % 7)) for n in range(12, 42))
        capture = _write_capture(
            self.root / "many.zip", "session-many",
            [_message(f"event-{i}", content, float(i+1)) for i, content in enumerate(contents)],
            complete=False,
        )
        ingest_artifact(capture, self.corpus)
        for page_limit, char_limit in ((1, 1), (3, 7), (7, 19)):
            with self.subTest(page_limit=page_limit, char_limit=char_limit):
                actual = ["" for _ in contents]
                observed = [set() for _ in contents]
                token = None
                seen = set()
                for page_no in range(600):
                    page = read_session(self.corpus, "session-many", cursor=token,
                                        limit=page_limit, max_chars=char_limit)
                    self.assertEqual(page["session_coverage"], "PARTIAL_SESSION_SLICE")
                    self.assertTrue(page["messages"])
                    self.assertLessEqual(len(page["messages"]), page_limit)
                    self.assertLessEqual(sum(len(m["text"]) for m in page["messages"]), char_limit)
                    for item in page["messages"]:
                        pos = item["ordinal"]
                        self.assertEqual(item["message_id"], f"event-{pos}")
                        self.assertEqual(item["text_offset"], len(actual[pos]))
                        self.assertNotIn(item["text_offset"], observed[pos])
                        observed[pos].add(item["text_offset"])
                        actual[pos] += item["text"]
                    if page_no == 2:
                        # A read-only cursor must be safe to replay after a lost response.
                        replay = read_session(self.corpus, "session-many", cursor=token,
                                              limit=page_limit, max_chars=char_limit)
                        self.assertEqual(replay, page)
                    if not page["has_more"]:
                        self.assertIsNone(page["next_cursor"])
                        break
                    self.assertIsNotNone(page["next_cursor"])
                    self.assertNotEqual(page["next_cursor"], token)
                    self.assertNotIn(page["next_cursor"], seen)
                    seen.add(page["next_cursor"])
                    token = page["next_cursor"]
                else:
                    self.fail("pagination failed to terminate")
                self.assertGreater(page_no, 3)
                self.assertEqual(actual, contents)
                # End of indexed messages is NOT proof of full original history.
                self.assertEqual(page["session_coverage"], "PARTIAL_SESSION_SLICE")

    def test_well_formed_cursor_position_edit_fails_closed(self):
        page = read_session(self.corpus, "session-a", limit=1)
        token = page["next_cursor"]
        self.assertIsNotNone(token)
        payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        payload["o"] = 2  # Valid-looking position, would silently skip m2.
        edited = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        with self.assertRaisesRegex(ValueError, "CURSOR_INVALID"):
            read_session(self.corpus, "session-a", cursor=edited)

    def test_malformed_cursor_and_cross_corpus_rejected(self):
        first = read_session(self.corpus, "session-a", limit=1)
        for bad in ("!", "", "not-base64!", "e30", "e30."):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "CURSOR_INVALID"):
                read_session(self.corpus, "session-a", cursor=bad)
        separate = self.root / "other-corpus"
        ingest_artifact(_write_capture(
            self.root / "other.zip", "session-a",
            [_message("one", "diff", 1.0)],
            complete=True,
        ), separate)
        with self.assertRaisesRegex(ValueError, "STALE_CURSOR"):
            read_session(separate, "session-a", cursor=first["next_cursor"])

    def test_rejects_integer_positions_outside_sqlite_bounds(self):
        with self.assertRaisesRegex(ValueError, "ORDINAL_OUT_OF_BOUNDS"):
            read_session(self.corpus, "session-a", start_ordinal=1 << 70)

    def test_mid_read_projection_change_fails_closed(self):
        from session_search import session_read

        first = session_read._projection_stamp(CorpusPaths.from_root(self.corpus).db)
        with patch.object(session_read, "_projection_stamp", side_effect=[first, "changed"]):
            with self.assertRaisesRegex(ValueError, "PROJECTION_CHANGED_DURING_READ"):
                read_session(self.corpus, "session-a", limit=1)

    def test_oversized_message_yields_explicit_continuation(self):
        first = read_session(self.corpus, "session-a", limit=1, max_chars=3)
        self.assertEqual(first["messages"][0]["text"], "aga")
        self.assertEqual(first["messages"][0]["text_offset"], 0)
        self.assertEqual(first["messages"][0]["text_total_bytes"], 5)
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
