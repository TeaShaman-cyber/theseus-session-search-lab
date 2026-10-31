import csv
import json
import sqlite3
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from session_search.shard_bundle import (
    BUNDLE_SCHEMA,
    build_bundle,
    verify_bundle,
)
from tests.test_neon_materializer import NeonMaterializerSourceTest


class ShardBundleTest(unittest.TestCase):
    def make_corpus(self, root: Path) -> Path:
        return NeonMaterializerSourceTest().make_corpus(root)

    def test_build_is_byte_deterministic_and_manifest_is_public_safe(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = self.make_corpus(td / "corpus")
            first = build_bundle(corpus, provider="xai", shard_id="xai-0001", output_path=td / "a.zip", require_verified=False)
            second = build_bundle(corpus, provider="xai", shard_id="xai-0001", output_path=td / "b.zip", require_verified=False)
            self.assertEqual((td / "a.zip").read_bytes(), (td / "b.zip").read_bytes())
            self.assertEqual(first, second)
            self.assertEqual(first["schema"], BUNDLE_SCHEMA)
            self.assertEqual(first["session_count"], 2)
            self.assertEqual(first["message_count"], 2)
            payload = json.dumps(first, sort_keys=True)
            self.assertNotIn("private-s1", payload)
            self.assertNotIn("hello", payload)

    def test_bundle_contains_only_contract_files_and_hidden_rows_are_absent(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = self.make_corpus(td / "corpus")
            path = td / "bundle.zip"
            build_bundle(corpus, provider="xai", shard_id="xai-0001", output_path=path, require_verified=False)
            with ZipFile(path) as zf:
                self.assertEqual(sorted(zf.namelist()), ["manifest.json", "messages.csv", "sessions.csv"])
                messages = zf.read("messages.csv").decode("utf-8")
                self.assertIn("fallback-2", messages)
                self.assertNotIn("hidden-id", messages)
                self.assertNotIn("secret", messages)

    def test_mixed_provider_corpus_selects_exact_deepseek_shard(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = self.make_corpus(td / "corpus")
            deep_sha = "c" * 64
            accepted = corpus / "ledger" / "accepted"
            (accepted / f"{deep_sha}.json").write_text(
                json.dumps({"artifact_sha256": deep_sha})
            )
            import sqlite3
            con = sqlite3.connect(corpus / "corpus.sqlite3")
            con.execute(
                "INSERT INTO sessions(session_id,title,coverage_state,coverage_reason,first_message_time,last_message_time,first_accepted_at) VALUES (?,?,?,?,?,?,?)",
                ("deep-private", "Deep", "COMPLETE_EXPOSED_CONVERSATION", "ok", 5.0, 6.0, "2026-01-03T00:00:00+00:00"),
            )
            con.execute(
                "INSERT INTO messages(row_id,session_id,ordinal,message_id,local_identity,canonical_message_sha256,role,content_type,search_class,create_time,provider_order,text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (4, "deep-private", 0, "dm1", "dl1", deep_sha, "user", "text", "dialogue", 5.0, 0, "deep signal"),
            )
            con.execute(
                "INSERT INTO artifacts(artifact_id,sha256,size_bytes,source_schema,source_adapter,observed_title,accepted_at,coverage_state,session_id,ledger_sha256,observed_message_count) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (3, deep_sha, 1, "portable", "deepseek-export", "deep", "2026-01-03T00:00:00+00:00", "COMPLETE_EXPOSED_CONVERSATION", "deep-private", deep_sha, 1),
            )
            con.execute(
                "INSERT INTO artifact_routes(artifact_id,accepted_session_id,projected_session_id,route_state,route_reason,route_version) VALUES (?,?,?,?,?,?)",
                (3, "deep-private", "deep-private", "DIRECT", "fixture", "v1"),
            )
            con.commit(); con.close()

            path = td / "deep.zip"
            built = build_bundle(
                corpus, provider="deepseek", shard_id="deepseek-0001",
                output_path=path, require_verified=False,
            )
            self.assertEqual(built["session_count"], 1)
            self.assertEqual(built["message_count"], 1)
            self.assertEqual(built["accepted_artifact_count"], 3)
            with ZipFile(path) as zf:
                manifest = json.loads(zf.read("manifest.json"))
                sessions = zf.read("sessions.csv").decode("utf-8")
                messages = zf.read("messages.csv").decode("utf-8")
            self.assertEqual(manifest["source_adapters"], ["deepseek-export"])
            self.assertIn("deep-private", sessions)
            self.assertIn("deep signal", messages)
            self.assertNotIn("private-s1", sessions)
            self.assertNotIn("hello", messages)

    def test_verify_accepts_large_text_field_and_restores_csv_limit(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = self.make_corpus(td / "corpus")
            large_text = "x" * 200_000
            with sqlite3.connect(corpus / "corpus.sqlite3") as con:
                con.execute(
                    "UPDATE messages SET text=? WHERE row_id=(SELECT min(row_id) FROM messages)",
                    (large_text,),
                )
                con.commit()
            path = td / "large-field.zip"
            build_bundle(
                corpus, provider="xai", shard_id="xai-0001",
                output_path=path, require_verified=False,
            )
            previous = csv.field_size_limit()
            try:
                csv.field_size_limit(1024)
                verified = verify_bundle(path)
                self.assertEqual(verified["message_count"], 2)
                self.assertEqual(csv.field_size_limit(), 1024)
            finally:
                csv.field_size_limit(previous)

    def test_build_rejects_field_above_bundle_contract_limit(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = self.make_corpus(td / "corpus")
            too_large = "x" * (8 * 1024 * 1024 + 1)
            with sqlite3.connect(corpus / "corpus.sqlite3") as con:
                con.execute(
                    "UPDATE messages SET text=? WHERE row_id=(SELECT min(row_id) FROM messages)",
                    (too_large,),
                )
                con.commit()
            with self.assertRaisesRegex(ValueError, "BUNDLE_CSV_FIELD_TOO_LARGE"):
                build_bundle(
                    corpus, provider="xai", shard_id="xai-0001",
                    output_path=td / "too-large.zip", require_verified=False,
                )

    def test_verify_rejects_tampered_member(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = self.make_corpus(td / "corpus")
            path = td / "bundle.zip"
            build_bundle(corpus, provider="xai", shard_id="xai-0001", output_path=path, require_verified=False)
            with ZipFile(path, "r") as src:
                members = {name: src.read(name) for name in src.namelist()}
            members["messages.csv"] = b"tampered\n"
            tampered = td / "tampered-member.zip"
            with ZipFile(tampered, "w") as dst:
                for name in ("manifest.json", "sessions.csv", "messages.csv"):
                    dst.writestr(name, members[name])
            with self.assertRaisesRegex(ValueError, "BUNDLE_FILE_DIGEST_MISMATCH"):
                verify_bundle(tampered)

    def test_verify_round_trip_reports_only_aggregate_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = self.make_corpus(td / "corpus")
            path = td / "bundle.zip"
            built = build_bundle(corpus, provider="xai", shard_id="xai-0001", output_path=path, require_verified=False)
            verified = verify_bundle(path)
            self.assertEqual(verified["bundle_sha256"], built["bundle_sha256"])
            self.assertEqual(verified["generation_id"], built["generation_id"])
            self.assertEqual(verified["message_count"], 2)
            self.assertNotIn("private-s1", json.dumps(verified, sort_keys=True))

    def test_verify_rejects_generation_identity_tamper(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = self.make_corpus(td / "corpus")
            source = td / "bundle.zip"
            tampered = td / "tampered.zip"
            build_bundle(
                corpus, provider="xai", shard_id="xai-0001",
                output_path=source, require_verified=False,
            )
            with ZipFile(source, "r") as src:
                members = {name: src.read(name) for name in src.namelist()}
            manifest = json.loads(members["manifest.json"].decode("utf-8"))
            manifest["generation_id"] = "0" * 64
            members["manifest.json"] = (
                json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            ).encode("utf-8")
            with ZipFile(tampered, "w") as dst:
                for name in ("manifest.json", "sessions.csv", "messages.csv"):
                    dst.writestr(name, members[name])
            with self.assertRaisesRegex(ValueError, "BUNDLE_GENERATION_ID_MISMATCH"):
                verify_bundle(tampered)

    def test_verify_cli_works_without_pythonpath(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = self.make_corpus(td / "corpus")
            bundle = td / "bundle.zip"
            build_bundle(
                corpus, provider="xai", shard_id="xai-0001",
                output_path=bundle, require_verified=False,
            )
            env = os.environ.copy(); env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, "scripts/shard_bundle.py", "verify", "--bundle", str(bundle)],
                cwd=root, env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["message_count"], 2)



if __name__ == "__main__":
    unittest.main()
