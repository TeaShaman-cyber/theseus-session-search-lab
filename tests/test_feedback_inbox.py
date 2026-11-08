import json
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest

from session_search.feedback_inbox import read_feedback_records, submit_feedback

ROOT = pathlib.Path(__file__).resolve().parents[1]


def request():
    return {
        "schema": "theseus.session-search-feedback-request.v0",
        "baseline_request_ref": "a" * 32,
        "result_request_ref": "b" * 32,
        "failure_class": "noisy_query",
        "operator": {"name": "functional_rephrase", "version": "v1"},
        "outcome": "improved",
        "notes": "private note that must not enter the pattern projection",
    }


class FeedbackInboxTest(unittest.TestCase):
    def test_submit_is_private_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            store = pathlib.Path(td) / "inbox"
            first = submit_feedback(store, request(), received_at="2026-11-08T12:00:00Z")
            second = submit_feedback(store, request(), received_at="2026-11-08T13:00:00Z")
            self.assertEqual(first, second)
            rows = read_feedback_records(store)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["received_at"], "2026-11-08T12:00:00Z")
            self.assertEqual(rows[0]["feedback_ref"], first)
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(next(store.iterdir()).stat().st_mode), 0o600)
            self.assertEqual(len(list(store.iterdir())), 1)

    def test_rejects_invalid_and_tampered_records(self):
        with tempfile.TemporaryDirectory() as td:
            store = pathlib.Path(td) / "inbox"
            bad = request()
            bad["query_text"] = "sensitive"
            with self.assertRaisesRegex(ValueError, "FEEDBACK_PRIVATE_TEXT_FORBIDDEN"):
                submit_feedback(store, bad)
            self.assertFalse(store.exists())
            ref = submit_feedback(store, request())
            path = store / f"{ref}.json"
            record = json.loads(path.read_text())
            record["feedback"]["outcome"] = "regressed"
            path.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "FEEDBACK_RECORD_REF_MISMATCH"):
                read_feedback_records(store)

    def test_agent_cli_submit_and_refresh_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            store = root / "inbox"
            cmd = [sys.executable, "-m", "session_search.feedback_inbox"]
            submit = subprocess.run(
                cmd + ["submit", "--store-dir", str(store)],
                input=json.dumps(request()),
                text=True, capture_output=True, cwd=ROOT, check=True,
            )
            reply = json.loads(submit.stdout)
            self.assertEqual(len(reply["feedback_ref"]), 32)
            self.assertNotIn("private note", submit.stdout)
            episodes = root / "output" / "experience.jsonl"
            patterns = root / "output" / "patterns.json"
            refresh = subprocess.run(
                cmd + ["refresh", "--store-dir", str(store),
                       "--episodes", str(episodes), "--patterns", str(patterns)],
                text=True, capture_output=True, cwd=ROOT, check=True,
            )
            self.assertEqual(json.loads(refresh.stdout)["episodes"], 1)
            payload = json.loads(patterns.read_text())
            self.assertEqual(payload["episode_count"], 1)
            self.assertEqual(payload["policy_promotion"], "NOT_PERFORMED")
            self.assertNotIn("private note", episodes.read_text())
            self.assertEqual(stat.S_IMODE(episodes.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
