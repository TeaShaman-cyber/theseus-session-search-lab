import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.alternative_review_status import classify_alternative_review, normalize_comments

HEAD = "a" * 40


def receipt(*, head=HEAD, reviewers=None, status="CLEAN", association="OWNER", updated_at="2026-11-22T03:00:00Z"):
    if reviewers is None:
        reviewers = [
            {
                "provider_route": "bothub-api",
                "model": "gemini-3.5-flash",
                "verdict": "CLEAN",
                "result_sha256": "b" * 64,
            },
            {
                "provider_route": "nvidi-nim",
                "model": "nvidia/nemotron-3-ultra-550b-a55b",
                "verdict": "CLEAN",
                "result_sha256": "c" * 64,
            },
        ]
    payload = {
        "head_sha": head,
        "status": status,
        "reviewers": reviewers,
        "reviewed_at": "2026-11-22T03:00:00Z",
        "operator_version": "manual-v1",
    }
    return {
        "user": {"login": "TeaShaman-cyber"},
        "author_association": association,
        "updated_at": updated_at,
        "body": (
            "<!-- alternative-review-state:v1 "
            + json.dumps(payload, separators=(",", ":"))
            + " -->"
        ),
    }


class AlternativeReviewStatusTests(unittest.TestCase):
    def test_two_distinct_clean_reviewers_on_exact_head_are_clean(self):
        result = classify_alternative_review([receipt()], HEAD)
        self.assertEqual(result["status"], "CLEAN")
        self.assertEqual(result["reviewed_commit"], HEAD)
        self.assertEqual(result["reviewer_count"], 2)

    def test_stale_receipt_is_stale(self):
        result = classify_alternative_review([receipt(head="d" * 40)], HEAD)
        self.assertEqual(result["status"], "STALE")
        self.assertEqual(result["reason"], "review-not-for-head")

    def test_untrusted_author_is_ignored(self):
        result = classify_alternative_review([receipt(association="NONE")], HEAD)
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["reason"], "trusted-review-receipt-missing")

    def test_one_reviewer_is_invalid(self):
        one = [{
            "provider_route": "bothub-api",
            "model": "gemini-3.5-flash",
            "verdict": "CLEAN",
            "result_sha256": "b" * 64,
        }]
        result = classify_alternative_review([receipt(reviewers=one)], HEAD)
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["reason"], "insufficient-distinct-reviewers")

    def test_duplicate_reviewer_identity_is_invalid(self):
        dup = [
            {
                "provider_route": "bothub-api",
                "model": "gemini-3.5-flash",
                "verdict": "CLEAN",
                "result_sha256": "b" * 64,
            },
            {
                "provider_route": "bothub-api",
                "model": "gemini-3.5-flash",
                "verdict": "CLEAN",
                "result_sha256": "c" * 64,
            },
        ]
        result = classify_alternative_review([receipt(reviewers=dup)], HEAD)
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["reason"], "insufficient-distinct-reviewers")

    def test_any_findings_are_blocking(self):
        reviewers = [
            {
                "provider_route": "bothub-api",
                "model": "gemini-3.5-flash",
                "verdict": "CLEAN",
                "result_sha256": "b" * 64,
            },
            {
                "provider_route": "nvidi-nim",
                "model": "nvidia/nemotron-3-ultra-550b-a55b",
                "verdict": "FINDINGS",
                "result_sha256": "c" * 64,
            },
        ]
        result = classify_alternative_review(
            [receipt(reviewers=reviewers, status="FINDINGS")], HEAD
        )
        self.assertEqual(result["status"], "FINDINGS")
        self.assertEqual(result["reason"], "reviewer-findings")

    def test_malformed_marker_is_invalid(self):
        comment = receipt()
        comment["body"] = "<!-- alternative-review-state:v1 {not-json} -->"
        result = classify_alternative_review([comment], HEAD)
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(result["reason"], "malformed-review-receipt")

    def test_paginated_comments_are_flattened(self):
        self.assertEqual(len(normalize_comments([[receipt()], [receipt()]])), 2)

    def test_wrapper_expands_arguments_and_repo_for_live_api_shape(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            stub = td_path / "gh"
            comments = [[receipt()]]
            stub.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' '"
                + json.dumps(comments, separators=(',', ':'))
                + "'\n",
                encoding="utf-8",
            )
            stub.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{td}:{env['PATH']}"
            env["GITHUB_REPOSITORY"] = "TeaShaman-cyber/theseus-session-search-lab"
            proc = subprocess.run(
                [
                    str(root / "tools" / "pr" / "alternative-review-status.sh"),
                    "321",
                    HEAD,
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result["status"], "CLEAN")
            self.assertEqual(result["reviewed_commit"], HEAD)



if __name__ == "__main__":
    unittest.main()
