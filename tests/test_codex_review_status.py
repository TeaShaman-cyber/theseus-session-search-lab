import json
import unittest

from scripts.codex_review_status import classify_codex_review

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


if __name__ == "__main__":
    unittest.main()
