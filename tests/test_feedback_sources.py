import json
import unittest

from session_search.feedback_sources import scan_upstash_feedback_records
from session_search.retrieval_experience import feedback_ref_for_request


def request(seed: str, outcome: str = "improved"):
    return {
        "schema": "theseus.session-search-feedback-request.v0",
        "baseline_request_ref": seed * 32,
        "result_request_ref": None,
        "failure_class": "noisy_query",
        "operator": {"name": "light_sonar_rephrase", "version": "v1"},
        "outcome": outcome,
        "notes": None,
    }


def record(req: dict, received_at: str):
    ref = feedback_ref_for_request(req)
    return {
        "schema": "theseus.session-search-feedback-record.v0",
        "feedback_ref": ref,
        "received_at": received_at,
        "feedback": req,
    }


class UpstashFeedbackSourceTest(unittest.TestCase):
    def test_hscan_paginates_validates_and_sorts_records(self):
        a = record(request("a"), "2026-11-08T01:00:00.000Z")
        b = record(request("b", "regressed"), "2026-11-08T01:01:00.000Z")
        calls = []

        def post(command):
            calls.append(command)
            cursor = str(command[2])
            if cursor == "0":
                return {"result": ["7", [b["feedback_ref"], json.dumps(b)]]}
            self.assertEqual(cursor, "7")
            return {"result": ["0", [a["feedback_ref"], json.dumps(a)]]}

        rows = scan_upstash_feedback_records(
            rest_url="https://example.upstash.io",
            token="secret",
            hash_key="theseus:session-search:feedback:v0",
            post_json=post,
        )
        self.assertEqual(
            [row["feedback_ref"] for row in rows],
            sorted([a["feedback_ref"], b["feedback_ref"]]),
        )
        self.assertEqual(calls[0], [
            "HSCAN",
            "theseus:session-search:feedback:v0",
            "0",
            "COUNT",
            500,
        ])
        self.assertEqual(calls[1][2], "7")

    def test_hscan_rejects_field_record_identity_mismatch(self):
        a = record(request("a"), "2026-11-08T01:00:00.000Z")

        def post(_command):
            return {"result": ["0", ["f" * 32, json.dumps(a)]]}

        with self.assertRaisesRegex(ValueError, "FEEDBACK_SOURCE_FIELD_MISMATCH"):
            scan_upstash_feedback_records(
                rest_url="https://example.upstash.io",
                token="secret",
                post_json=post,
            )

    def test_hscan_rejects_duplicate_feedback_ref_across_pages(self):
        a = record(request("a"), "2026-11-08T01:00:00.000Z")
        calls = 0

        def post(_command):
            nonlocal calls
            calls += 1
            cursor = "9" if calls == 1 else "0"
            return {"result": [cursor, [a["feedback_ref"], json.dumps(a)]]}

        with self.assertRaisesRegex(ValueError, "FEEDBACK_SOURCE_DUPLICATE_REF"):
            scan_upstash_feedback_records(
                rest_url="https://example.upstash.io",
                token="secret",
                post_json=post,
            )

    def test_hscan_rejects_cursor_loop(self):
        def post(_command):
            return {"result": ["7", []]}

        with self.assertRaisesRegex(ValueError, "FEEDBACK_SOURCE_CURSOR_LOOP"):
            scan_upstash_feedback_records(
                rest_url="https://example.upstash.io",
                token="secret",
                post_json=post,
            )

    def test_source_config_requires_https(self):
        with self.assertRaisesRegex(ValueError, "FEEDBACK_SOURCE_URL_INVALID"):
            scan_upstash_feedback_records(
                rest_url="http://example.upstash.io",
                token="secret",
                post_json=lambda _: {"result": ["0", []]},
            )


if __name__ == "__main__":
    unittest.main()
