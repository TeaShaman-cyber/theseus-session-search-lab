import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from session_search.corpus_store import CorpusPaths, ingest_artifact
from session_search.shard_integrity import verify_shard_source_integrity
from tests.test_corpus_store import _message, _write_capture
from tests.test_chatgpt_export import ChatGPTExportAdapterTest


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

    def test_chatgpt_branch_match_shard_replays_complete_family_closure(self):
        from session_search.chatgpt_export import ingest_export, materialize_export
        from session_search.artifact import normalize_artifact

        helper = ChatGPTExportAdapterTest()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = helper.write_export(
                root, [helper.conversation(cid="route-alt")]
            )
            children = materialize_export(source, root / "children")
            by_id = {normalize_artifact(path).session_id: path for path in children}
            alt_id = next(
                sid for sid in by_id if sid.startswith("route-alt~branch-")
            )
            raw = helper.write_raw_capture(
                root / "raw-alt.zip",
                "route-alt",
                [
                    helper.message("m-u", "user", "text", ["question"], 10.0),
                    helper.message("m-b", "assistant", "text", ["answer B"], 30.0),
                    helper.message(
                        "provider-extra",
                        "assistant",
                        "text",
                        ["provider only"],
                        35.0,
                    ),
                ],
            )
            corpus = root / "corpus"
            self.assertEqual(ingest_export(source, corpus)["status"], "COMPLETE")
            self.assertEqual(ingest_artifact(raw, corpus)["status"], "INGESTED")

            with sqlite3.connect(CorpusPaths.from_root(corpus).db) as con:
                route = con.execute(
                    """
                    SELECT r.projected_session_id,r.route_state
                    FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id
                    WHERE a.sha256=?
                    """,
                    (normalize_artifact(raw).artifact_sha256,),
                ).fetchone()
                self.assertEqual(route, (alt_id, "BRANCH_MATCH"))

            result = verify_shard_source_integrity(
                corpus, provider="chatgpt", shard_id="chatgpt-0001"
            )
            self.assertEqual(result["status"], "VERIFIED")
            self.assertIn("BRANCH_MATCH", result["route_states"])
            self.assertGreaterEqual(result["closure_artifact_count"], 3)

    def test_unrelated_corrupted_blob_is_outside_chatgpt_family_closure(self):
        from session_search.chatgpt_export import ingest_export, materialize_export
        from session_search.artifact import normalize_artifact
        from session_search.neon_materializer import _load_rows_for_sessions

        helper = ChatGPTExportAdapterTest()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = helper.write_export(
                root, [helper.conversation(cid="route-alt")]
            )
            materialize_export(source, root / "children")
            raw = helper.write_raw_capture(
                root / "raw-alt.zip",
                "route-alt",
                [
                    helper.message("m-u", "user", "text", ["question"], 10.0),
                    helper.message("m-b", "assistant", "text", ["answer B"], 30.0),
                    helper.message(
                        "provider-extra",
                        "assistant",
                        "text",
                        ["provider only"],
                        35.0,
                    ),
                ],
            )
            unrelated = _write_capture(
                root / "unrelated.zip",
                "unrelated-session",
                [_message("unrelated", "outside closure", 50.0, role="assistant")],
                complete=True,
            )
            corpus = root / "corpus"
            self.assertEqual(ingest_export(source, corpus)["status"], "COMPLETE")
            self.assertEqual(ingest_artifact(raw, corpus)["status"], "INGESTED")
            unrelated_result = ingest_artifact(unrelated, corpus)
            self.assertEqual(unrelated_result["status"], "INGESTED")

            db = CorpusPaths.from_root(corpus).db
            with sqlite3.connect(db) as con:
                session_ids = tuple(
                    row[0]
                    for row in con.execute(
                        "SELECT session_id FROM sessions WHERE session_id=? OR session_id LIKE ? ORDER BY session_id",
                        ("route-alt", "route-alt~%"),
                    )
                )
                sessions, messages = _load_rows_for_sessions(con, session_ids)
                marks = ",".join("?" for _ in session_ids)
                adapters = [
                    str(row[0])
                    for row in con.execute(
                        "SELECT DISTINCT a.source_adapter FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id "
                        f"WHERE r.projected_session_id IN ({marks}) ORDER BY a.source_adapter",
                        session_ids,
                    )
                ]

            unrelated_blob = (
                CorpusPaths.from_root(corpus).artifacts_sha256
                / f"{unrelated_result['artifact_sha256']}.zip"
            )
            unrelated_blob.write_bytes(b"deliberately corrupted outside family closure")

            fake_shard = {
                "provider": "chatgpt",
                "shard_id": "chatgpt-0001",
                "sessions": len(sessions),
                "searchable_messages": len(messages),
                "searchable_text_bytes": sum(
                    len(row[6].encode("utf-8")) for row in messages
                ),
            }
            with mock.patch(
                "session_search.shard_integrity._select_shard_assignment",
                return_value=(fake_shard, session_ids, adapters),
            ):
                result = verify_shard_source_integrity(
                    corpus, provider="chatgpt", shard_id="chatgpt-0001"
                )

            self.assertEqual(result["status"], "VERIFIED")
            self.assertIn("BRANCH_MATCH", result["route_states"])
            self.assertLess(result["closure_artifact_count"], result["accepted_artifact_count"])

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
            self.assertEqual(result["status"], "RECONCILIATION_REQUIRED")
            self.assertEqual(result["reason"], "routed artifact replay mismatch")

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
