import hashlib
import json
import pathlib
import tempfile
import unittest
import zipfile

from session_search.corpus_store import CorpusPaths, verify_corpus
from session_search.handoff import run_handoff


def write_capture(path: pathlib.Path, session_id: str, text: str) -> pathlib.Path:
    payload = {
        "conversation_id": session_id,
        "title": session_id,
        "page_info": {"has_previous_page": False, "has_next_page": False},
        "messages": [{
            "id": f"{session_id}-m1",
            "author": {"role": "user"},
            "create_time": 1770000000.0,
            "content": {"content_type": "text", "parts": [text]},
        }],
    }
    raw = json.dumps(payload, sort_keys=True).encode()
    member = "optional/conversation-1.bin"
    manifest = {
        "schema": "barn-doctor-export:v1",
        "files": [{"name": member, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        zf.writestr(member, raw)
    return path


class HandoffTest(unittest.TestCase):
    def test_deterministic_scan_is_idempotent_and_preserves_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            inbox = root / "inbox"
            corpus = root / "corpus"
            inbox.mkdir()
            write_capture(inbox / "b.zip", "session-b", "beta")
            write_capture(inbox / "a.zip", "session-a", "alpha")
            (inbox / "ignore.txt").write_text("not an artifact")

            first = run_handoff(inbox, corpus)
            self.assertEqual(first["status"], "COMPLETE")
            self.assertEqual([pathlib.Path(row["source"]).name for row in first["results"]], ["a.zip", "b.zip"])
            self.assertEqual([row["status"] for row in first["results"]], ["INGESTED", "INGESTED"])

            second = run_handoff(inbox, corpus)
            self.assertEqual(second["status"], "COMPLETE")
            self.assertEqual([row["status"] for row in second["results"]], ["ALREADY_INGESTED", "ALREADY_INGESTED"])
            self.assertTrue((inbox / "a.zip").exists())
            self.assertTrue((inbox / "b.zip").exists())
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

            receipts = sorted((CorpusPaths.from_root(corpus).root / "receipts" / "handoff").glob("*.json"))
            self.assertEqual(len(receipts), 4)
            self.assertEqual({json.loads(p.read_text())["status"] for p in receipts}, {"INGESTED", "ALREADY_INGESTED"})

    def test_failed_artifact_gets_receipt_and_does_not_block_later_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            inbox = root / "inbox"
            corpus = root / "corpus"
            inbox.mkdir()
            bad = inbox / "a-bad.zip"
            bad.write_bytes(b"not-a-zip")
            write_capture(inbox / "b-good.zip", "session-good", "good canary")

            result = run_handoff(inbox, corpus)
            self.assertEqual(result["status"], "DEGRADED")
            self.assertEqual([row["status"] for row in result["results"]], ["FAILED", "INGESTED"])
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

            failure_receipt = pathlib.Path(result["results"][0]["receipt"])
            receipt = json.loads(failure_receipt.read_text())
            self.assertEqual(receipt["status"], "FAILED")
            self.assertEqual(receipt["source_sha256"], hashlib.sha256(bad.read_bytes()).hexdigest())
            self.assertIn("error_class", receipt)


if __name__ == "__main__":
    unittest.main()
