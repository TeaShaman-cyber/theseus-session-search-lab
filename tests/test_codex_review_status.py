import json
import unittest
from pathlib import Path

from scripts.codex_review_status import (
    classify_codex_review,
    bounded_poll_sleep_seconds,
    classify_codex_wait,
    classify_pr_head_currentness,
    normalize_comments,
)

HEAD = "a" * 40


def summary(state, updated_at="2026-10-24T20:00:00Z"):
    payload = {
        "code_review": state,
        "security_review": {
            "commit_sha": None,
            "has_any_findings": None,
            "has_unresolved_findings": None,
            "status": None,
        },
    }
    return {
        "user": {"login": "chatgpt-codex-connector[bot]"},
        "updated_at": updated_at,
        "body": (
            "<!-- codex-pull-request-review-summary -->\n"
            f"<!-- codex-review-state:v1 {json.dumps(payload, separators=(',', ':'))} -->\n"
            "## Codex Review Summary"
        ),
    }


class CodexReviewStatusTest(unittest.TestCase):
    def test_running_review_is_pending(self):
        result = classify_codex_review([
            summary({
                "commit_sha": HEAD,
                "has_any_findings": None,
                "has_unresolved_findings": None,
                "status": "running",
            })
        ], HEAD)
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["reason"], "review-running")
        self.assertTrue(result["advisory"])

    def test_completed_review_with_findings_is_advisory_findings(self):
        result = classify_codex_review([
            summary({
                "commit_sha": HEAD,
                "has_any_findings": True,
                "has_unresolved_findings": True,
                "status": "completed",
            })
        ], HEAD)
        self.assertEqual(result["status"], "FINDINGS")
        self.assertEqual(result["reason"], "unresolved-findings")
        self.assertTrue(result["advisory"])

    def test_completed_clean_review_on_exact_head_is_clean(self):
        result = classify_codex_review([
            summary({
                "commit_sha": HEAD,
                "has_any_findings": False,
                "has_unresolved_findings": False,
                "status": "completed",
            })
        ], HEAD)
        self.assertEqual(result["status"], "CLEAN")
        self.assertEqual(result["reviewed_commit"], HEAD)

    def test_stale_review_is_stale(self):
        stale = "b" * 40
        result = classify_codex_review([
            summary({
                "commit_sha": stale,
                "has_any_findings": False,
                "has_unresolved_findings": False,
                "status": "completed",
            })
        ], HEAD)
        self.assertEqual(result["status"], "STALE")
        self.assertEqual(result["reason"], "review-not-for-head")

    def test_missing_summary_is_pending(self):
        result = classify_codex_review([], HEAD)
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["reason"], "review-summary-missing")

    def test_paginated_comment_pages_are_flattened(self):
        first = summary({
            "commit_sha": "b" * 40,
            "has_any_findings": False,
            "has_unresolved_findings": False,
            "status": "completed",
        }, updated_at="2026-10-24T19:00:00Z")
        second = summary({
            "commit_sha": HEAD,
            "has_any_findings": True,
            "has_unresolved_findings": True,
            "status": "completed",
        }, updated_at="2026-10-24T21:00:00Z")
        comments = normalize_comments([[first], [second]])
        result = classify_codex_review(comments, HEAD)
        self.assertEqual(result["status"], "FINDINGS")
        self.assertEqual(result["reviewed_commit"], HEAD)

    def test_expected_head_mismatch_is_pending_currentness(self):
        observed = "b" * 40
        result = classify_pr_head_currentness(observed, HEAD)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["reason"], "pr-head-not-current")
        self.assertEqual(result["head_sha"], HEAD)
        self.assertEqual(result["observed_head_sha"], observed)
        self.assertIsNone(result["reviewed_commit"])
        self.assertIsNone(classify_pr_head_currentness(HEAD, HEAD))

    def test_wait_clean_is_terminal_immediately(self):
        state = {"status": "CLEAN", "head_sha": HEAD, "advisory": True}
        result = classify_codex_wait(state, elapsed_seconds=1, timeout_seconds=120)
        self.assertEqual(result["wait_disposition"], "TERMINAL")

    def test_wait_findings_is_terminal_evidence_not_merge_decision(self):
        state = {"status": "FINDINGS", "head_sha": HEAD, "advisory": True}
        result = classify_codex_wait(state, elapsed_seconds=2, timeout_seconds=120)
        self.assertEqual(result["wait_disposition"], "TERMINAL")
        self.assertEqual(result["status"], "FINDINGS")

    def test_wait_pending_becomes_timeout_at_bound(self):
        state = {"status": "PENDING", "head_sha": HEAD, "advisory": True}
        before = classify_codex_wait(state, elapsed_seconds=119, timeout_seconds=120)
        at_bound = classify_codex_wait(state, elapsed_seconds=120, timeout_seconds=120)
        self.assertEqual(before["wait_disposition"], "WAIT")
        self.assertEqual(at_bound["wait_disposition"], "TIMEOUT")

    def test_wait_stale_becomes_timeout_at_bound(self):
        state = {"status": "STALE", "head_sha": HEAD, "advisory": True}
        before = classify_codex_wait(state, elapsed_seconds=10, timeout_seconds=20)
        at_bound = classify_codex_wait(state, elapsed_seconds=20, timeout_seconds=20)
        self.assertEqual(before["wait_disposition"], "WAIT")
        self.assertEqual(at_bound["wait_disposition"], "TIMEOUT")

    def test_poll_sleep_is_capped_at_remaining_timeout(self):
        self.assertEqual(
            bounded_poll_sleep_seconds(
                elapsed_seconds=0, timeout_seconds=1, poll_seconds=3
            ),
            1,
        )
        self.assertEqual(
            bounded_poll_sleep_seconds(
                elapsed_seconds=119, timeout_seconds=120, poll_seconds=10
            ),
            1,
        )
        self.assertEqual(
            bounded_poll_sleep_seconds(
                elapsed_seconds=120, timeout_seconds=120, poll_seconds=10
            ),
            0,
        )

    def test_wrapper_requests_all_comment_pages(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "tools" / "pr" / "codex-review-status.sh").read_text(encoding="utf-8")
        self.assertIn("--paginate", text)
        self.assertIn("--slurp", text)

        wait = (root / "tools" / "pr" / "codex-review-wait.sh").read_text(encoding="utf-8")
        self.assertIn("classify_codex_wait", wait)
        self.assertIn("EXPECTED_HEAD=${2:-}", wait)
        self.assertIn("TIMEOUT=${3:-120}", wait)
        self.assertIn('codex-review-status.sh" "$PR" "$EXPECTED_HEAD"', wait)
        self.assertNotIn("pr merge", wait)


if __name__ == "__main__":
    unittest.main()
