import copy
import hashlib
import json
import pathlib
import sqlite3
import zipfile
import tempfile
import unittest

from session_search.corpus_store import (
    CorpusMutationLock,
    CorpusPaths,
    accepted_entry_path,
    init_corpus_db,
    ingest_artifact,
    resolve_corpus_root,
    write_accepted_entry,
)


def _message(message_id, text, create_time, *, role="assistant", metadata=None):
    message = {
        "author": {"role": role},
        "create_time": create_time,
        "content": {"content_type": "text", "parts": [text]},
    }
    if message_id is not None:
        message["id"] = message_id
    if metadata is not None:
        message["metadata"] = metadata
    return message


def _write_capture(path, session_id, messages, *, complete=False, title="Synthetic session"):
    payload = {
        "conversation_id": session_id,
        "title": title,
        "page_info": {"has_previous_page": not complete, "has_next_page": False},
        "messages": messages,
    }
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    member = "optional/conversation-1.bin"
    manifest = {
        "schema": "barn-doctor-export:v1",
        "files": [{"name": member, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        zf.writestr(member, data)
    return path


def _db_scalar(corpus, sql, params=()):
    db = CorpusPaths.from_root(corpus).db
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchone()[0]


def _db_column(corpus, sql, params=()):
    db = CorpusPaths.from_root(corpus).db
    with sqlite3.connect(db) as conn:
        return [row[0] for row in conn.execute(sql, params)]


def _counts(corpus):
    return {
        "sessions": _db_scalar(corpus, "SELECT count(*) FROM sessions"),
        "artifacts": _db_scalar(corpus, "SELECT count(*) FROM artifacts"),
        "messages": _db_scalar(corpus, "SELECT count(*) FROM messages"),
        "sources": _db_scalar(corpus, "SELECT count(*) FROM message_sources"),
        "fts": _db_scalar(corpus, "SELECT count(*) FROM messages_fts"),
    }


def _fts_count(corpus, phrase):
    db = CorpusPaths.from_root(corpus).db
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?", (phrase,)
        ).fetchone()[0]


def _coverage(corpus, session_id="session-a"):
    return _db_scalar(corpus, "SELECT coverage_state FROM sessions WHERE session_id=?", (session_id,))



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


class CorpusIngestTest(unittest.TestCase):
    def test_two_sessions_coexist_and_same_message_id_does_not_cross_collide(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            a = _write_capture(root / "a.zip", "session-a", [_message("m1", "alpha", 1.0)])
            b = _write_capture(root / "b.zip", "session-b", [_message("m1", "beta", 1.0)])
            self.assertEqual(ingest_artifact(a, corpus)["status"], "INGESTED")
            self.assertEqual(ingest_artifact(b, corpus)["status"], "INGESTED")
            self.assertEqual(_db_scalar(corpus, "SELECT count(*) FROM sessions"), 2)
            self.assertEqual(_db_scalar(corpus, "SELECT count(*) FROM messages WHERE message_id='m1'"), 2)

    def test_same_artifact_sha_is_verified_noop(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            capture = _write_capture(root / "a.zip", "session-a", [_message("m1", "alpha", 1.0)])
            first = ingest_artifact(capture, corpus)
            before = _counts(corpus)
            second = ingest_artifact(capture, corpus)
            self.assertEqual(first["status"], "INGESTED")
            self.assertEqual(second["status"], "ALREADY_INGESTED")
            self.assertEqual(second["mutation"], "none")
            self.assertEqual(_counts(corpus), before)

    def test_repeated_same_session_capture_adds_only_novel_messages_and_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            m1 = _message("m1", "stable phrase", 1.0)
            first = _write_capture(root / "a.zip", "session-a", [m1])
            second = _write_capture(root / "b.zip", "session-a", [copy.deepcopy(m1), _message("m2", "novel phrase", 2.0)])
            ingest_artifact(first, corpus)
            before = _counts(corpus)
            ingest_artifact(second, corpus)
            after = _counts(corpus)
            self.assertEqual(after["messages"], before["messages"] + 1)
            self.assertGreater(after["sources"], before["sources"])
            self.assertEqual(_db_scalar(corpus, "SELECT count(*) FROM messages WHERE message_id='m1'"), 1)

    def test_same_canonical_message_with_changed_irrelevant_metadata_deduplicates(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            first_msg = _message("m1", "stable", 1.0, metadata={"ui": "old"})
            second_msg = _message("m1", "stable", 1.0, metadata={"ui": "new"})
            first = _write_capture(root / "a.zip", "session-a", [first_msg])
            second = _write_capture(root / "b.zip", "session-a", [second_msg])
            ingest_artifact(first, corpus)
            ingest_artifact(second, corpus)
            self.assertEqual(_db_scalar(corpus, "SELECT count(*) FROM messages WHERE message_id='m1'"), 1)
            self.assertEqual(len(set(_db_column(corpus, "SELECT source_object_sha256 FROM message_sources"))), 2)

    def test_changed_canonical_payload_for_same_session_message_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            first = _write_capture(root / "a.zip", "session-a", [_message("m1", "before", 1.0)])
            bad = _write_capture(root / "b.zip", "session-a", [_message("m1", "after", 1.0)])
            ingest_artifact(first, corpus)
            before = _counts(corpus)
            with self.assertRaisesRegex(RuntimeError, "FAILED_CONFLICTING_DUPLICATE"):
                ingest_artifact(bad, corpus)
            self.assertEqual(_counts(corpus), before)

    def test_anonymous_messages_from_two_artifacts_are_not_merged(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            first_msg = _message(None, "anonymous", 1.0, metadata={"capture": "first"})
            second_msg = _message(None, "anonymous", 1.0, metadata={"capture": "second"})
            first = _write_capture(root / "a.zip", "session-a", [first_msg])
            second = _write_capture(root / "b.zip", "session-a", [second_msg])
            self.assertNotEqual(hashlib.sha256(first.read_bytes()).hexdigest(), hashlib.sha256(second.read_bytes()).hexdigest())
            ingest_artifact(first, corpus)
            ingest_artifact(second, corpus)
            self.assertEqual(_db_scalar(corpus, "SELECT count(*) FROM messages WHERE message_id IS NULL"), 2)

    def test_failed_ingest_leaves_prior_counts_and_fts_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            first = _write_capture(root / "a.zip", "session-a", [_message("m1", "stable phrase", 1.0)])
            bad = _write_capture(root / "b.zip", "session-a", [_message("m1", "changed phrase", 1.0)])
            ingest_artifact(first, corpus)
            before = _counts(corpus)
            before_hits = _fts_count(corpus, '"stable"')
            with self.assertRaisesRegex(RuntimeError, "FAILED_CONFLICTING_DUPLICATE"):
                ingest_artifact(bad, corpus)
            self.assertEqual(_counts(corpus), before)
            self.assertEqual(_fts_count(corpus, '"stable"'), before_hits)

    def test_partial_complete_and_later_tail_coverage_transitions_are_conservative(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            m1 = _message("m1", "first", 1.0)
            m2 = _message("m2", "second", 2.0)
            partial = _write_capture(root / "partial.zip", "session-a", [copy.deepcopy(m1)], complete=False)
            complete = _write_capture(root / "complete.zip", "session-a", [copy.deepcopy(m1)], complete=True)
            tail = _write_capture(root / "tail.zip", "session-a", [copy.deepcopy(m2)], complete=False)
            newer_complete = _write_capture(root / "new-complete.zip", "session-a", [copy.deepcopy(m1), copy.deepcopy(m2)], complete=True)
            ingest_artifact(partial, corpus)
            self.assertEqual(_coverage(corpus), "PARTIAL_SESSION_SLICE")
            ingest_artifact(complete, corpus)
            self.assertEqual(_coverage(corpus), "COMPLETE_EXPOSED_CONVERSATION")
            ingest_artifact(tail, corpus)
            self.assertEqual(_coverage(corpus), "PARTIAL_SESSION_SLICE")
            ingest_artifact(newer_complete, corpus)
            self.assertEqual(_coverage(corpus), "COMPLETE_EXPOSED_CONVERSATION")

    def test_unaccepted_blob_after_failed_ingest_is_not_registered(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            first = _write_capture(root / "a.zip", "session-a", [_message("m1", "before", 1.0)])
            bad = _write_capture(root / "b.zip", "session-a", [_message("m1", "after", 1.0)])
            ingest_artifact(first, corpus)
            bad_sha = hashlib.sha256(bad.read_bytes()).hexdigest()
            with self.assertRaisesRegex(RuntimeError, "FAILED_CONFLICTING_DUPLICATE"):
                ingest_artifact(bad, corpus)
            paths = CorpusPaths.from_root(corpus)
            self.assertTrue((paths.artifacts_sha256 / f"{bad_sha}.zip").exists())
            self.assertFalse(accepted_entry_path(paths, bad_sha).exists())
            self.assertEqual(_db_scalar(corpus, "SELECT count(*) FROM artifacts WHERE sha256=?", (bad_sha,)), 0)


if __name__ == "__main__":
    unittest.main()
