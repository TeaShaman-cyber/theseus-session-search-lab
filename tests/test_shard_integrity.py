import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from session_search.corpus_store import CorpusPaths, ingest_artifact
from session_search.shard_integrity import verify_shard_source_integrity
from tests.test_corpus_store import _message, _write_capture


class ShardSourceIntegrityTest(unittest.TestCase):
    def make_corpus(self, root: Path) -> Path:
        corpus = root / "corpus"
        first = _write_capture(
            root / "first.zip",
            "session-one",
            [_message("m1", "alpha signal", 1.0, role="user")],
            complete=True,
        )
        second = _write_capture(
            root / "second.zip",
            "session-two",
            [_message("m2", "beta signal", 2.0, role="assistant")],
            complete=True,
        )
        ingest_artifact(first, corpus)
        ingest_artifact(second, corpus)
        return corpus

    def test_direct_shard_replays_from_accepted_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = self.make_corpus(Path(td))
            result = verify_shard_source_integrity(
                corpus, provider="chatgpt", shard_id="chatgpt-0001"
            )
            self.assertEqual(result["status"], "VERIFIED")
            self.assertEqual(result["session_count"], 2)
            self.assertEqual(result["message_count"], 2)
            self.assertEqual(result["shard_artifact_count"], 2)
            self.assertEqual(result["route_states"], ["DIRECT"])
            payload = json.dumps(result, sort_keys=True)
            self.assertNotIn("session-one", payload)
            self.assertNotIn("alpha signal", payload)

    def test_missing_direct_route_fails_before_replay_subset_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            corpus = root / "corpus"
            first = _write_capture(
                root / "first.zip",
                "shared-session",
                [_message("m1", "first accepted message", 1.0, role="user")],
                complete=False,
            )
            second = _write_capture(
                root / "second.zip",
                "shared-session",
                [_message("m2", "second accepted message", 2.0, role="assistant")],
                complete=True,
            )
            ingest_artifact(first, corpus)
            ingest_artifact(second, corpus)
            before = verify_shard_source_integrity(
                corpus, provider="chatgpt", shard_id="chatgpt-0001"
            )
            self.assertEqual(before["status"], "VERIFIED")
            self.assertEqual(before["shard_artifact_count"], 2)
            self.assertEqual(before["message_count"], 2)

            with sqlite3.connect(CorpusPaths.from_root(corpus).db) as con:
                con.execute(
                    "DELETE FROM artifact_routes WHERE artifact_id=(SELECT max(artifact_id) FROM artifacts)"
                )

            result = verify_shard_source_integrity(
                corpus, provider="chatgpt", shard_id="chatgpt-0001"
            )
            self.assertEqual(result["status"], "RECONCILIATION_REQUIRED")
            self.assertEqual(result["reason"], "artifact route membership disagreement")
            self.assertEqual(result["projection_artifact_count"], 2)
            self.assertEqual(result["route_count"], 1)
            self.assertEqual(result["missing_route_count"], 1)
            self.assertEqual(result["orphan_route_count"], 0)

    def test_projection_tamper_is_detected_by_source_replay(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = self.make_corpus(Path(td))
            with sqlite3.connect(CorpusPaths.from_root(corpus).db) as con:
                con.execute(
                    "UPDATE messages SET text='tampered projection' WHERE session_id='session-one'"
                )
            result = verify_shard_source_integrity(
                corpus, provider="chatgpt", shard_id="chatgpt-0001"
            )
            self.assertEqual(result["status"], "RECONCILIATION_REQUIRED")
            self.assertEqual(result["reason"], "searchable message projection replay mismatch")

    def test_non_direct_route_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = self.make_corpus(Path(td))
            with sqlite3.connect(CorpusPaths.from_root(corpus).db) as con:
                con.execute(
                    "UPDATE artifact_routes SET route_state='BRANCH_MATCH' WHERE artifact_id=(SELECT min(artifact_id) FROM artifacts)"
                )
            result = verify_shard_source_integrity(
                corpus, provider="chatgpt", shard_id="chatgpt-0001"
            )
            self.assertEqual(result["status"], "UNSUPPORTED_ROUTE_CLOSURE")
            self.assertEqual(result["route_states"], ["BRANCH_MATCH", "DIRECT"])

    def test_cli_works_without_pythonpath(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            corpus = self.make_corpus(Path(td))
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/verify_shard_source.py",
                    "--corpus",
                    str(corpus),
                    "--provider",
                    "chatgpt",
                    "--shard-id",
                    "chatgpt-0001",
                ],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result["status"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
