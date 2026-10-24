import json
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
