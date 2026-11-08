"""Portable private feedback-record exchange contract for local inbox replicas."""
import copy
import json
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest

from session_search.feedback_inbox import (
    export_feedback_records,
    import_feedback_records,
    read_feedback_records,
    submit_feedback,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
STAMP = "2026-11-08T10:00:00Z"


def request(index: str):
    return {
        "schema": "theseus.session-search-feedback-request.v0",
        "baseline_request_ref": index * 32,
        "result_request_ref": None,
        "failure_class": "no_signal",
        "operator": {"name": "strict_to_recall", "version": "v1"},
        "outcome": "unknown",
        "notes": "PRIVATE: note stays in private exchange",
    }


class FeedbackExchangeTests(unittest.TestCase):
    def test_roundtrip_is_private_exact_idempotent_and_reversible(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            a, b = root / "local", root / "mirror"
            ids = [
                submit_feedback(a, request("a"), received_at=STAMP),
                submit_feedback(a, request("b"), received_at=STAMP),
            ]
            out = root / "exchange.jsonl"
            self.assertEqual(export_feedback_records(a, out), 2)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
            self.assertEqual(len(out.read_text().splitlines()), 2)
            self.assertIn("PRIVATE:", out.read_text())  # private exchange is NOT public pattern output
            self.assertEqual(import_feedback_records(out, b), (2, 0))
            self.assertEqual(import_feedback_records(out, b), (0, 2))
            self.assertEqual(read_feedback_records(a), read_feedback_records(b))
            self.assertEqual({row["feedback_ref"] for row in read_feedback_records(b)}, set(ids))
            mirror = root / "mirror-exchange.jsonl"
            export_feedback_records(b, mirror)
            self.assertEqual(out.read_bytes(), mirror.read_bytes())
            self.assertEqual(import_feedback_records(mirror, a), (0, 2))
            with self.assertRaises(FileExistsError):
                export_feedback_records(a, out)

    def test_conflicting_same_id_different_receipt_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            a, b = root / "local", root / "mirror"
            ref = submit_feedback(a, request("a"), received_at=STAMP)
            submit_feedback(b, request("a"), received_at="2026-11-08T11:00:00Z")
            batch = root / "batch.jsonl"
            export_feedback_records(a, batch)
            before = read_feedback_records(b)
            with self.assertRaisesRegex(ValueError, "FEEDBACK_EXCHANGE_CONFLICT"):
                import_feedback_records(batch, b)
            self.assertEqual(read_feedback_records(b), before)
            self.assertEqual(read_feedback_records(b)[0]["feedback_ref"], ref)

    def test_invalid_duplicate_and_mixed_conflict_are_preflighted(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            a, b = root / "local", root / "mirror"
            submit_feedback(a, request("a"), received_at=STAMP)
            submit_feedback(a, request("b"), received_at=STAMP)
            record_a, record_b = read_feedback_records(a)
            batch = root / "batch.jsonl"
            batch.write_text(json.dumps(record_a) + "\n" + json.dumps(record_a) + "\n")
            batch.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "FEEDBACK_EXCHANGE_DUPLICATE_REF"):
                import_feedback_records(batch, b)
            self.assertFalse(b.exists())
            bad = copy.deepcopy(record_b)
            bad["feedback"]["outcome"] = "improved"
            batch.write_text(json.dumps(record_a) + "\n" + json.dumps(bad) + "\n")
            with self.assertRaisesRegex(ValueError, "FEEDBACK_RECORD_REF_MISMATCH"):
                import_feedback_records(batch, b)
            self.assertFalse(b.exists())
            batch.write_text(json.dumps(record_a) + "\n" + json.dumps(record_b) + "\n")
            batch.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "FEEDBACK_EXCHANGE_NOT_PRIVATE"):
                import_feedback_records(batch, b)
            self.assertFalse(b.exists())

    def test_cli_outputs_counts_not_raw_notes(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source, target = root / "local", root / "replica"
            submit_feedback(source, request("a"), received_at=STAMP)
            batch = root / "batch.jsonl"
            cmd = [sys.executable, "-m", "session_search.feedback_inbox"]
            export = subprocess.run(
                cmd + ["export", "--store-dir", str(source), "--output", str(batch)],
                cwd=ROOT, text=True, capture_output=True, check=True,
            )
            self.assertEqual(json.loads(export.stdout)["records"], 1)
            self.assertNotIn("PRIVATE:", export.stdout)
            imported = subprocess.run(
                cmd + ["import", "--source", str(batch), "--store-dir", str(target)],
                cwd=ROOT, text=True, capture_output=True, check=True,
            )
            self.assertEqual(json.loads(imported.stdout), {"imported": 1, "unchanged": 0})
            self.assertNotIn("PRIVATE:", imported.stdout)


if __name__ == "__main__":
    unittest.main()
