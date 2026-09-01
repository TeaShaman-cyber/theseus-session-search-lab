import json
import pathlib
import sqlite3
import tempfile
import unittest

from session_search.corpus_store import (
    CorpusMutationLock,
    CorpusPaths,
    accepted_entry_path,
    init_corpus_db,
    resolve_corpus_root,
    write_accepted_entry,
)


class CorpusPrimitiveTest(unittest.TestCase):
    def test_explicit_path_overrides_environment_default(self):
        env = {"SESSION_SEARCH_CORPUS": "/env/corpus"}
        self.assertEqual(
            resolve_corpus_root("/explicit/corpus", env),
            pathlib.Path("/explicit/corpus"),
        )
        self.assertEqual(
            resolve_corpus_root(None, env),
            pathlib.Path("/env/corpus"),
        )

    def test_missing_corpus_location_is_explicit_error(self):
        with self.assertRaisesRegex(ValueError, "CORPUS_LOCATION_UNRESOLVED"):
            resolve_corpus_root(None, {})

    def test_second_writer_fails_fast_without_breaking_first_lock(self):
        with tempfile.TemporaryDirectory() as td:
            paths = CorpusPaths.from_root(pathlib.Path(td))
            paths.ensure_layout()
            with CorpusMutationLock(paths, "ingest"):
                self.assertTrue(paths.mutation_lock.is_dir())
                with self.assertRaisesRegex(RuntimeError, "CORPUS_MUTATION_LOCKED"):
                    with CorpusMutationLock(paths, "rebuild"):
                        pass
                self.assertTrue(paths.mutation_lock.is_dir())
            self.assertFalse(paths.mutation_lock.exists())

    def test_accepted_entry_is_atomic_and_hash_addressed(self):
        with tempfile.TemporaryDirectory() as td:
            paths = CorpusPaths.from_root(pathlib.Path(td))
            paths.ensure_layout()
            sha = "a" * 64
            entry = {
                "schema": "theseus.session-search-accepted-artifact.v1",
                "artifact_sha256": sha,
                "size_bytes": 123,
                "session_id": "session-a",
                "coverage_state": "PARTIAL_SESSION_SLICE",
                "source_schema": "barn-doctor-export:v1",
                "source_adapter": "barn-doctor",
                "accepted_at": "2026-09-01T00:00:00+00:00",
            }
            path = write_accepted_entry(paths, entry)
            self.assertEqual(path, accepted_entry_path(paths, sha))
            self.assertEqual(json.loads(path.read_text()), entry)
            self.assertFalse(any(paths.staging.iterdir()))

    def test_schema_supports_many_sessions_and_scoped_message_identity(self):
        conn = sqlite3.connect(":memory:")
        try:
            init_corpus_db(conn)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                )
            }
            self.assertTrue(
                {
                    "corpus_meta",
                    "artifacts",
                    "sessions",
                    "payload_pages",
                    "messages",
                    "message_sources",
                    "messages_fts",
                }
                <= tables
            )
            index_sql = "\n".join(
                row[0] or ""
                for row in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index'"
                )
            )
            self.assertIn("messages_identified_identity", index_sql)
            self.assertIn("session_id", index_sql)
            self.assertIn("message_id", index_sql)
            self.assertIn("WHERE message_id IS NOT NULL", index_sql)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
