"""Issue 60: full-verifier session metadata checks must be set-based."""

import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

import session_search.corpus_store as store
from test_corpus_store import _message, _write_capture


class SessionMetadataBatchVerifierTest(unittest.TestCase):
    def _build_corpus(self, root: pathlib.Path, sessions: int = 5) -> pathlib.Path:
        corpus = root / "corpus"
        captures = []
        for i in range(sessions):
            sid = f"session-{i}"
            captures.append(
                _write_capture(
                    root / f"partial-{i}.zip",
                    sid,
                    [_message(f"p-{i}", f"partial {i}", float(i + 1))],
                    title=f"partial-title-{i}",
                )
            )
            if i == 1:
                captures.append(
                    _write_capture(
                        root / "partial-1-tie.zip",
                        sid,
                        [_message("p-1-tie", "same-time alternate", float(i + 1))],
                        title="same-time-alternate-title",
                    )
                )
            if i % 2 == 0:
                captures.append(
                    _write_capture(
                        root / f"complete-{i}.zip",
                        sid,
                        [
                            _message(f"p-{i}", f"partial {i}", float(i + 1)),
                            _message(f"c-{i}", f"complete {i}", float(i + 20)),
                        ],
                        complete=True,
                        title=f"complete-title-{i}",
                    )
                )
        self.assertEqual(store.ingest_many(captures, corpus)["status"], "COMPLETE")
        return corpus

    def test_batch_values_match_per_session_oracle_including_orphan_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = self._build_corpus(root)
            db = store.CorpusPaths.from_root(corpus).db
            with sqlite3.connect(db) as raw:
                raw.execute(
                    """
                    INSERT INTO sessions(
                        session_id,title,coverage_state,coverage_reason,
                        first_message_time,last_message_time,first_accepted_at,last_accepted_at,
                        title_source_time,title_source_artifact_sha256
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "orphan-session",
                        "",
                        "PARTIAL_SESSION_SLICE",
                        "no_single_complete_artifact_covers_all_messages",
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                    ),
                )
                raw.commit()
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            try:
                session_ids = [
                    row[0] for row in conn.execute("SELECT session_id FROM sessions")
                ]
                oracle = {
                    sid: store._session_metadata_values(conn, sid)
                    for sid in session_ids
                }
                batched = store._all_session_metadata_values(conn)
            finally:
                conn.close()
            self.assertEqual(batched, oracle)

    def test_full_projection_verifier_has_no_session_parameterized_metadata_scans(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = self._build_corpus(root, sessions=9)
            paths = store.CorpusPaths.from_root(corpus)
            seen = []
            original = store._open_existing_projection

            def tracing_open(path):
                conn = original(path)
                conn.set_trace_callback(seen.append)
                return conn

            with mock.patch.object(
                store, "_open_existing_projection", side_effect=tracing_open
            ):
                result = store._verify_projection(paths, paths.db)
            self.assertEqual(result["status"], "VERIFIED")
            repeated = [
                query
                for query in seen
                if "WHERE r.projected_session_id=" in query
                or "SELECT count(*) FROM messages WHERE session_id=" in query
                or "WHERE p.artifact_id=" in query
                or "min(create_time),max(create_time) FROM messages WHERE session_id="
                in query
            ]
            self.assertEqual(repeated, [], repeated[:8])


if __name__ == "__main__":
    unittest.main()
