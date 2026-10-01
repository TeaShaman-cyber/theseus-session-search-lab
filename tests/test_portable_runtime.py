import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build_portable_runtime.py"


class PortableRuntimeBuildTest(unittest.TestCase):
    def _source_tree(self, root: pathlib.Path) -> pathlib.Path:
        source = root / "source"
        (source / "session_search").mkdir(parents=True)
        (source / "docs").mkdir(parents=True)
        (source / "session_search" / "__init__.py").write_text("")
        modules = (
            "corpus",
            "search",
            "deepseek_export",
            "xai_export",
            "speed_booster_export",
            "chatgpt_export",
            "claude_export",
            "barn_recovery",
            "handoff",
            "refresh_readiness",
        )
        for index, module in enumerate(modules, start=1):
            (source / "session_search" / f"{module}.py").write_text(
                f"VALUE = {index}\n"
            )
        (source / "session_search" / "reconciliation.py").write_text("VALUE = 'reconciliation'\n")
        (source / "docs" / "portable-runtime.md").write_text("# Portable runtime\n")
        return source

    def _build(self, source: pathlib.Path, out_dir: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        revision = "a" * 40
        subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--source-root",
                str(source),
                "--source-repo",
                "TeaShaman-cyber/theseus-session-search-lab",
                "--source-revision",
                revision,
                "--out-dir",
                str(out_dir),
            ],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        return out_dir / "session-search-runtime.zip", out_dir / "session-search-runtime.receipt.json"

    def test_builder_is_deterministic_and_binds_member_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self._source_tree(root)
            zip_a, receipt_a = self._build(source, root / "a")
            zip_b, receipt_b = self._build(source, root / "b")

            self.assertEqual(zip_a.read_bytes(), zip_b.read_bytes())
            self.assertEqual(receipt_a.read_bytes(), receipt_b.read_bytes())

            receipt = json.loads(receipt_a.read_text())
            self.assertEqual(receipt["schema"], "theseus.session-search-portable-runtime-receipt.v1")
            self.assertEqual(receipt["source"], {
                "repo": "TeaShaman-cyber/theseus-session-search-lab",
                "revision": "a" * 40,
            })
            self.assertEqual(receipt["artifact"]["sha256"], hashlib.sha256(zip_a.read_bytes()).hexdigest())
            self.assertEqual(receipt["artifact"]["size_bytes"], zip_a.stat().st_size)

            with zipfile.ZipFile(zip_a) as zf:
                names = zf.namelist()
                self.assertEqual(names, sorted(names))
                self.assertIn("runtime-manifest.json", names)
                self.assertIn("PORTABLE_RUNTIME.md", names)
                self.assertIn("session_search/__init__.py", names)
                self.assertIn("session_search/reconciliation.py", names)
                self.assertIn("session_search/refresh_readiness.py", names)
                manifest_raw = zf.read("runtime-manifest.json")
                manifest = json.loads(manifest_raw)
                self.assertEqual(manifest["schema"], "theseus.session-search-portable-runtime.v1")
                self.assertEqual(manifest["python_min"], "3.11")
                self.assertEqual(manifest["source"], receipt["source"])
                self.assertEqual(receipt["manifest_sha256"], hashlib.sha256(manifest_raw).hexdigest())
                self.assertIn(
                    "python3 -m session_search.chatgpt_export",
                    manifest["entrypoints"],
                )
                self.assertIn(
                    "python3 -m session_search.claude_export",
                    manifest["entrypoints"],
                )
                self.assertIn(
                    "python3 -m session_search.refresh_readiness",
                    manifest["entrypoints"],
                )
                for entrypoint in manifest["entrypoints"]:
                    module = entrypoint.removeprefix("python3 -m ")
                    self.assertIn(module.replace(".", "/") + ".py", names)
                expected = {
                    item["path"]: item["sha256"]
                    for item in manifest["members"]
                }
                for path, digest in expected.items():
                    self.assertEqual(hashlib.sha256(zf.read(path)).hexdigest(), digest)

    def test_builder_rejects_missing_runtime_contract_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "source"
            (source / "session_search").mkdir(parents=True)
            (source / "session_search" / "__init__.py").write_text("")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(BUILDER),
                    "--source-root",
                    str(source),
                    "--source-repo",
                    "example/repo",
                    "--source-revision",
                    "b" * 40,
                    "--out-dir",
                    str(root / "out"),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("portable-runtime.md", proc.stderr)

    def test_hosted_consumer_is_independent_of_repository_checkout(self):
        workflow = (ROOT / ".github" / "workflows" / "portable-runtime.yml").read_text()
        consumer = workflow.split("  consume-runtime:", 1)[1]
        self.assertNotIn("actions/checkout@", consumer)
        self.assertIn("actions/download-artifact@", consumer)
        self.assertIn("PORTABLE_RUNTIME_CONSUMER_ACCEPTANCE_PASS", consumer)



if __name__ == "__main__":
    unittest.main()
