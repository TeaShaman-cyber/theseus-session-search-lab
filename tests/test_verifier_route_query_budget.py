"""Issue 60: routing verifier query cost must not grow per accepted artifact."""

import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

import session_search.corpus_store as store
from test_corpus_store import _message, _write_capture


class RouteVerifierBatchTest(unittest.TestCase):
    def test_route_membership_reads_are_set_based(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            artifacts = [
                _write_capture(
                    root / f"artifact-{i}.zip",
                    f"session-{i % 3}",
                    [_message(f"message-{i}", f"text-{i}", float(i + 1))],
                )
                for i in range(8)
            ]
            self.assertEqual(store.ingest_many(artifacts, corpus)["status"], "COMPLETE")
            seen = []
            original = store._open_existing_projection

            def tracing_open(path):
                conn = original(path)
                conn.set_trace_callback(seen.append)
                return conn

            with mock.patch.object(store, "_open_existing_projection", side_effect=tracing_open):
                result = store.verify_corpus(corpus)
            self.assertEqual(result["status"], "VERIFIED")
            per_route = [
                q for q in seen
                if "SELECT count(*) FROM sessions WHERE session_id=" in q
                or ("SELECT count(*)" in q and "FROM payload_pages p" in q
                    and "JOIN artifacts a" in q)
            ]
            self.assertEqual(per_route, [], per_route[:5])

    def test_cross_session_payload_page_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            first = _write_capture(root / "a.zip", "session-a", [_message("a", "a-text", 1.0)])
            second = _write_capture(root / "b.zip", "session-b", [_message("b", "b-text", 2.0)])
            self.assertEqual(store.ingest_many([first, second], corpus)["status"], "COMPLETE")
            db = store.CorpusPaths.from_root(corpus).db
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE payload_pages SET session_id='session-b' WHERE session_id='session-a'")
            result = store.verify_corpus(corpus)
            self.assertEqual(result["status"], "RECONCILIATION_REQUIRED")
            self.assertEqual(result["reason"], "artifact page route mismatch")


if __name__ == "__main__":
    unittest.main()
