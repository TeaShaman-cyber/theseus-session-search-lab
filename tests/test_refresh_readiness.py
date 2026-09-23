import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from session_search.corpus_store import CorpusPaths
from session_search.handoff import run_handoff
from session_search.refresh_readiness import inspect_refresh_readiness
from tests.test_handoff import write_capture

ROOT = pathlib.Path(__file__).resolve().parents[1]


class RefreshReadinessTest(unittest.TestCase):
    def test_new_artifact_is_pending_until_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            inbox = root / "inbox"
            corpus = root / "corpus"
            inbox.mkdir()
            artifact = write_capture(inbox / "fresh.zip", "session-fresh", "fresh canary")

            before = inspect_refresh_readiness(inbox, corpus)
            self.assertEqual(before["state"], "NEW_ARTIFACTS_PENDING")
            self.assertEqual(before["zip_count"], 1)
            self.assertEqual(before["pending_count"], 1)
            self.assertEqual(before["accepted_count"], 0)
            self.assertEqual(before["pending"][0]["name"], "fresh.zip")
            self.assertEqual(
                before["pending"][0]["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest()
            )

            handoff = run_handoff(inbox, corpus)
            self.assertEqual(handoff["results"][0]["status"], "INGESTED")

            after = inspect_refresh_readiness(inbox, corpus)
            self.assertEqual(after["state"], "NO_NEW_ARTIFACTS")
            self.assertEqual(after["pending_count"], 0)
            self.assertEqual(after["accepted_count"], 1)

    def test_failed_unknown_zip_remains_pending_and_probe_is_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            inbox = root / "inbox"
            corpus = root / "corpus"
            inbox.mkdir()
            bad = inbox / "bad.zip"
            bad.write_bytes(b"not-a-valid-artifact")

            first = inspect_refresh_readiness(inbox, corpus)
            self.assertEqual(first["state"], "NEW_ARTIFACTS_PENDING")
            self.assertEqual(first["pending_count"], 1)

            paths = CorpusPaths.from_root(corpus)
            self.assertFalse(paths.root.exists())

            failed = run_handoff(inbox, corpus)
            self.assertEqual(failed["results"][0]["status"], "FAILED")
            receipt_count = len(list((paths.root / "receipts" / "handoff").glob("*.json")))

            second = inspect_refresh_readiness(inbox, corpus)
            self.assertEqual(second["state"], "NEW_ARTIFACTS_PENDING")
            self.assertEqual(second["pending_count"], 1)
            self.assertEqual(
                len(list((paths.root / "receipts" / "handoff").glob("*.json"))), receipt_count
            )

    def test_empty_inbox_is_no_new_artifacts_without_creating_corpus(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            inbox = root / "inbox"
            corpus = root / "corpus"
            inbox.mkdir()

            result = inspect_refresh_readiness(inbox, corpus)

            self.assertEqual(result["state"], "NO_NEW_ARTIFACTS")
            self.assertEqual(result["zip_count"], 0)
            self.assertEqual(result["pending_count"], 0)
            self.assertFalse(corpus.exists())

    def test_cli_reports_machine_readable_pending_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            inbox = root / "inbox"
            corpus = root / "corpus"
            inbox.mkdir()
            write_capture(inbox / "fresh.zip", "session-fresh", "fresh canary")

            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "session_search.refresh_readiness",
                    "--inbox",
                    str(inbox),
                    "--corpus",
                    str(corpus),
                    "--json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["state"], "NEW_ARTIFACTS_PENDING")
            self.assertEqual(payload["refresh_action"], "RUN_HANDOFF")
            self.assertEqual(
                payload["source_currentness"],
                "UNKNOWN_WITHOUT_SOURCE_WATERMARK",
            )
            self.assertFalse(corpus.exists())


if __name__ == "__main__":
    unittest.main()
