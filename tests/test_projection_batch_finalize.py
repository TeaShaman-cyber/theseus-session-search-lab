"""Issue 60: batch replay finalizes derived session state once per session."""

import pathlib
import tempfile
import unittest
from unittest import mock

import session_search.corpus_store as store
from test_corpus_store import _message, _write_capture


class ProjectionBatchFinalizeTest(unittest.TestCase):
    def test_batch_replay_defers_per_artifact_session_finalization(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            captures = [
                _write_capture(
                    root / f"capture-{i}.zip",
                    f"session-{i % 3}",
                    [_message(f"m-{i}", f"text {i}", float(i + 1))],
                )
                for i in range(9)
            ]
            self.assertEqual(store.ingest_many(captures, corpus)["status"], "COMPLETE")
            paths = store.CorpusPaths.from_root(corpus)
            entries = store.read_accepted_ledger(paths)
            candidate = root / "replayed.sqlite3"

            with (
                mock.patch.object(
                    store, "_recompute_ordinals", wraps=store._recompute_ordinals
                ) as ordinals,
                mock.patch.object(
                    store,
                    "_recompute_session_metadata",
                    wraps=store._recompute_session_metadata,
                ) as metadata,
            ):
                store._populate_projection_from_entries(paths, candidate, entries)

            self.assertEqual(ordinals.call_count, 3)
            self.assertEqual(metadata.call_count, 3)
            self.assertEqual(
                store._verify_projection(paths, candidate)["status"], "VERIFIED"
            )

    def test_single_projection_apply_keeps_immediate_finalization_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            source = _write_capture(
                root / "capture.zip",
                "session-a",
                [_message("m-1", "alpha", 1.0)],
            )
            artifact = store.normalize_artifact(source)
            paths = store.CorpusPaths.from_root(corpus)
            paths.ensure_layout()
            db = root / "single.sqlite3"
            import sqlite3

            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            store.init_corpus_db(conn)
            with (
                mock.patch.object(
                    store, "_recompute_ordinals", wraps=store._recompute_ordinals
                ) as ordinals,
                mock.patch.object(
                    store,
                    "_recompute_session_metadata",
                    wraps=store._recompute_session_metadata,
                ) as metadata,
            ):
                store.apply_normalized_artifact_to_projection(
                    conn, paths, artifact, "2026-11-01T00:00:00+00:00"
                )
            self.assertEqual(ordinals.call_count, 1)
            self.assertEqual(metadata.call_count, 1)
            conn.close()


if __name__ == "__main__":
    unittest.main()
