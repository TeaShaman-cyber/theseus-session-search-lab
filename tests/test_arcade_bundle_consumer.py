import asyncio
import ast
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_SRC = ROOT / "arcade" / "session-search-materializer" / "src"
if str(PKG_SRC) not in sys.path:
    sys.path.insert(0, str(PKG_SRC))

from session_search.shard_bundle import build_bundle
from session_search_arcade_materializer.bundle_consumer import (
    BundleVerificationError,
    download_drive_bundle,
    verify_bundle,
)
from tests.test_neon_materializer import NeonMaterializerSourceTest


class ArcadeBundleConsumerTest(unittest.TestCase):
    def make_bundle(self, root: Path) -> Path:
        corpus = NeonMaterializerSourceTest().make_corpus(root / "corpus")
        bundle = root / "bundle.zip"
        build_bundle(
            corpus,
            provider="xai",
            shard_id="xai-0001",
            output_path=bundle,
            require_verified=False,
        )
        return bundle

    def test_independent_consumer_accepts_producer_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            bundle = self.make_bundle(Path(td))
            expected = hashlib.sha256(bundle.read_bytes()).hexdigest()
            result = verify_bundle(bundle, expected_bundle_sha256=expected)
            self.assertEqual(result["status"], "VERIFIED")
            self.assertEqual(result["bundle_sha256"], expected)
            self.assertEqual(result["provider"], "xai")
            self.assertEqual(result["message_count"], 2)
            self.assertNotIn("private-s1", json.dumps(result, sort_keys=True))
            self.assertNotIn("hello", json.dumps(result, sort_keys=True))

    def test_valid_long_message_field_is_inspectable(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = NeonMaterializerSourceTest().make_corpus(td / "corpus")
            long_text = "x" * 140_000
            con = sqlite3.connect(corpus / "corpus.sqlite3")
            con.execute("UPDATE messages SET text=? WHERE row_id=1", (long_text,))
            con.commit()
            con.close()

            bundle = td / "bundle.zip"
            build_bundle(
                corpus,
                provider="xai",
                shard_id="xai-0001",
                output_path=bundle,
                require_verified=False,
            )
            expected = hashlib.sha256(bundle.read_bytes()).hexdigest()
            result = verify_bundle(bundle, expected_bundle_sha256=expected)
            self.assertEqual(result["status"], "VERIFIED")
            self.assertEqual(result["message_count"], 2)

    def test_expected_bundle_sha_is_required_and_must_match(self):
        with tempfile.TemporaryDirectory() as td:
            bundle = self.make_bundle(Path(td))
            with self.assertRaisesRegex(BundleVerificationError, "EXPECTED_BUNDLE_SHA256_INVALID"):
                verify_bundle(bundle, expected_bundle_sha256="bad")
            with self.assertRaisesRegex(BundleVerificationError, "BUNDLE_SHA256_MISMATCH"):
                verify_bundle(bundle, expected_bundle_sha256="0" * 64)

    def test_tampered_bundle_fails_closed_even_with_recomputed_outer_sha(self):
        from zipfile import ZipFile
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            source = self.make_bundle(td)
            tampered = td / "tampered.zip"
            with ZipFile(source, "r") as src:
                members = {name: src.read(name) for name in src.namelist()}
            members["messages.csv"] = b"tampered\n"
            with ZipFile(tampered, "w") as dst:
                for name in ("manifest.json", "sessions.csv", "messages.csv"):
                    dst.writestr(name, members[name])
            expected = hashlib.sha256(tampered.read_bytes()).hexdigest()
            with self.assertRaisesRegex(BundleVerificationError, "BUNDLE_FILE_DIGEST_MISMATCH"):
                verify_bundle(tampered, expected_bundle_sha256=expected)

    def test_drive_download_streams_and_checks_sha_before_acceptance(self):
        payload = b"abc123" * 1000
        expected = hashlib.sha256(payload).hexdigest()

        class Response:
            status_code = 200
            headers = {"content-length": str(len(payload))}
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def aiter_bytes(self):
                yield payload[:100]
                yield payload[100:]
            def raise_for_status(self): return None

        class Client:
            def stream(self, method, url, *, headers, params):
                self.method = method; self.url = url; self.headers = headers; self.params = params
                return Response()

        async def run():
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "bundle.zip"
                client = Client()
                result = await download_drive_bundle(
                    drive_file_id="1abc_DEF-123456",
                    oauth_token="private-token",
                    expected_bundle_sha256=expected,
                    destination=dest,
                    client=client,
                )
                self.assertEqual(result["sha256"], expected)
                self.assertEqual(result["size_bytes"], len(payload))
                self.assertEqual(dest.read_bytes(), payload)
                self.assertEqual(client.headers["Authorization"], "Bearer private-token")
                self.assertNotIn("private-token", json.dumps(result))
        asyncio.run(run())

    def test_drive_download_sha_mismatch_deletes_partial_destination(self):
        payload = b"wrong"

        class Response:
            headers = {}
            async def __aenter__(self): return self
            async def __aexit__(self, *_args): return False
            async def aiter_bytes(self): yield payload
            def raise_for_status(self): return None

        class Client:
            def stream(self, *_args, **_kwargs): return Response()

        async def run():
            with tempfile.TemporaryDirectory() as td:
                dest = Path(td) / "bundle.zip"
                with self.assertRaisesRegex(BundleVerificationError, "BUNDLE_SHA256_MISMATCH"):
                    await download_drive_bundle(
                        drive_file_id="1abc_DEF-123456",
                        oauth_token="private-token",
                        expected_bundle_sha256="0" * 64,
                        destination=dest,
                        client=Client(),
                    )
                self.assertFalse(dest.exists())
        asyncio.run(run())

    def test_hosted_entrypoint_supports_script_launch(self):
        server = PKG_SRC / "session_search_arcade_materializer" / "server.py"
        parsed = ast.parse(server.read_text())
        imports = [node for node in ast.walk(parsed) if isinstance(node, ast.ImportFrom)]
        self.assertTrue(any(
            node.level == 0 and node.module == "session_search_arcade_materializer.bundle_consumer"
            for node in imports
        ))
        workflow = (ROOT / ".github" / "workflows" / "deploy-session-search-arcade-materializer.yml").read_text()
        self.assertIn("python -m pip install -e .", workflow)

    def test_server_surface_is_read_only_inspection_only(self):
        server = (ROOT / "arcade" / "session-search-materializer" / "src" / "session_search_arcade_materializer" / "server.py").read_text()
        self.assertIn("drive.readonly", server)
        self.assertIn("inspect_bundle", server)
        self.assertNotIn("NEON_API_KEY", server)
        self.assertNotIn("psycopg", server)
        self.assertNotIn("mark_generation_ready", server)
        self.assertNotIn("activate_generation", server)


if __name__ == "__main__":
    unittest.main()
