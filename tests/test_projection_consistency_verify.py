import json
import pathlib
import sqlite3
import subprocess
import tempfile
import unittest

from session_search.corpus_store import (
    CorpusPaths,
    ingest_many,
    read_accepted_ledger,
    verify_corpus,
    verify_projection_consistency,
)
from test_corpus_store import _message, _write_capture


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProjectionConsistencyVerifyTest(unittest.TestCase):
    def _corpus(self, root: pathlib.Path) -> pathlib.Path:
        corpus = root / "corpus"
        captures = [
            _write_capture(
                root / f"capture-{i}.zip",
                f"session-{i}",
                [_message(f"m-{i}", f"message {i}", float(i + 1))],
            )
            for i in range(2)
        ]
        self.assertEqual(ingest_many(captures, corpus)["status"], "COMPLETE")
        return corpus

    def test_projection_mode_names_weaker_claim_and_counts(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = self._corpus(pathlib.Path(td))
            result = verify_projection_consistency(corpus)
            self.assertEqual(result["status"], "PROJECTION_CONSISTENT")
            self.assertEqual(result["verification_level"], "projection_consistency")
            self.assertFalse(result["derivation_verified"])
            self.assertFalse(result["artifact_bytes_verified"])
            self.assertEqual(result["artifacts"], 2)
            self.assertEqual(result["sessions"], 2)
            self.assertEqual(result["searchable_messages"], result["fts_rows"])

    def test_projection_mode_deliberately_does_not_hash_accepted_blobs(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = self._corpus(pathlib.Path(td))
            paths = CorpusPaths.from_root(corpus)
            sha = next(iter(read_accepted_ledger(paths)))
            blob = paths.artifacts_sha256 / f"{sha}.zip"
            blob.write_bytes(blob.read_bytes() + b"tamper")

            projection = verify_projection_consistency(corpus)
            self.assertEqual(projection["status"], "PROJECTION_CONSISTENT")
            self.assertFalse(projection["artifact_bytes_verified"])

            full = verify_corpus(corpus)
            self.assertEqual(full["status"], "FAILED_INTEGRITY")
            self.assertEqual(full["reason"], "accepted artifact hash/size mismatch")

    def test_projection_mode_detects_ledger_projection_membership_drift(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = self._corpus(pathlib.Path(td))
            db = CorpusPaths.from_root(corpus).db
            with sqlite3.connect(db) as conn:
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.execute(
                    "DELETE FROM artifacts WHERE artifact_id=(SELECT min(artifact_id) FROM artifacts)"
                )
                conn.commit()
            result = verify_projection_consistency(corpus)
            self.assertEqual(result["status"], "RECONCILIATION_REQUIRED")
            self.assertEqual(
                result["reason"], "accepted ledger/projection membership disagreement"
            )

    def test_cli_default_verify_remains_full_derivation_check(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = self._corpus(pathlib.Path(td))
            paths = CorpusPaths.from_root(corpus)
            sha = next(iter(read_accepted_ledger(paths)))
            blob = paths.artifacts_sha256 / f"{sha}.zip"
            blob.write_bytes(blob.read_bytes() + b"tamper")
            completed = subprocess.run(
                [
                    "python3",
                    "-m",
                    "session_search.corpus",
                    "verify",
                    "--corpus",
                    str(corpus),
                    "--json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 1)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "FAILED_INTEGRITY")
            self.assertEqual(result["reason"], "accepted artifact hash/size mismatch")

    def test_cli_projection_mode_emits_explicit_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = self._corpus(pathlib.Path(td))
            completed = subprocess.run(
                [
                    "python3",
                    "-m",
                    "session_search.corpus",
                    "verify",
                    "--corpus",
                    str(corpus),
                    "--mode",
                    "projection",
                    "--json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "PROJECTION_CONSISTENT")
            self.assertEqual(result["verification_level"], "projection_consistency")
            self.assertFalse(result["derivation_verified"])


if __name__ == "__main__":
    unittest.main()
