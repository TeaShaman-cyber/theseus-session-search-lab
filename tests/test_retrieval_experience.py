import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from session_search.retrieval_experience import (
    aggregate_patterns,
    load_jsonl,
    validate_episode,
    validate_feedback_request,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def episode(**overrides):
    value = {
        "schema": "theseus.session-search-retrieval-experience.v0",
        "episode_id": "ep-001",
        "observed_at": "2026-11-08T00:00:00Z",
        "failure_class": "noisy_query",
        "operator": {"name": "light_sonar_rephrase", "version": "v1"},
        "outcome": "improved",
        "confidence": "FACT",
        "aggregation_eligible": True,
        "baseline": {"status": "FOUND", "request_ref": "req-a"},
        "result": {"status": "FOUND", "request_ref": "req-b"},
        "provenance": [{"kind": "issue", "ref": "session-search#182"}],
    }
    value.update(overrides)
    return value


class RetrievalExperienceContractTest(unittest.TestCase):
    def test_feedback_request_is_explicit_and_does_not_need_query_plaintext(self):
        request = {
            "schema": "theseus.session-search-feedback-request.v0",
            "baseline_request_ref": "req-a",
            "result_request_ref": "req-b",
            "failure_class": "noisy_query",
            "operator": {"name": "light_sonar_rephrase", "version": "v1"},
            "outcome": "improved",
            "notes": "Functional rephrase reduced noise and recovered the intended history.",
        }
        self.assertEqual(validate_feedback_request(request), request)

    def test_feedback_request_rejects_raw_query_or_target_text(self):
        request = {
            "schema": "theseus.session-search-feedback-request.v0",
            "baseline_request_ref": "req-a",
            "failure_class": "no_signal",
            "operator": {"name": "strict_to_recall", "version": "v1"},
            "outcome": "unknown",
            "query_text": "private user query",
        }
        with self.assertRaisesRegex(ValueError, "FEEDBACK_PRIVATE_TEXT_FORBIDDEN"):
            validate_feedback_request(request)

    def test_feedback_request_rejects_unknown_fields(self):
        request = {
            "schema": "theseus.session-search-feedback-request.v0",
            "baseline_request_ref": "req-a",
            "failure_class": "no_signal",
            "operator": {"name": "strict_to_recall", "version": "v1"},
            "outcome": "unknown",
            "outocme": "typo-that-must-not-be-ignored",
        }
        with self.assertRaisesRegex(ValueError, "FEEDBACK_FIELDS_INVALID"):
            validate_feedback_request(request)

    def test_feedback_request_rejects_unknown_operator_fields(self):
        request = {
            "schema": "theseus.session-search-feedback-request.v0",
            "baseline_request_ref": "req-a",
            "failure_class": "no_signal",
            "operator": {
                "name": "strict_to_recall",
                "version": "v1",
                "prompt": "hidden implementation detail",
            },
            "outcome": "unknown",
        }
        with self.assertRaisesRegex(ValueError, "EXPERIENCE_OPERATOR_FIELDS_INVALID"):
            validate_feedback_request(request)

    def test_episode_rejects_private_text_at_any_depth(self):
        value = episode()
        value["result"]["metadata"] = {"query_text": "private nested query"}
        with self.assertRaisesRegex(ValueError, "EXPERIENCE_PRIVATE_TEXT_FORBIDDEN"):
            validate_episode(value)

    def test_episode_rejects_unknown_top_level_fields(self):
        value = episode(unexpected=True)
        with self.assertRaisesRegex(ValueError, "EXPERIENCE_FIELDS_INVALID"):
            validate_episode(value)

    def test_episode_requires_known_outcome_and_confidence(self):
        with self.assertRaisesRegex(ValueError, "EXPERIENCE_OUTCOME_INVALID"):
            validate_episode(episode(outcome="great"))
        with self.assertRaisesRegex(ValueError, "EXPERIENCE_CONFIDENCE_INVALID"):
            validate_episode(episode(confidence="MAYBE"))

    def test_aggregation_keeps_help_harm_and_unknown_separate(self):
        rows = [
            episode(episode_id="p", outcome="improved", aggregation_eligible=True),
            episode(episode_id="n", outcome="regressed", aggregation_eligible=True),
            episode(episode_id="u", outcome="unknown", aggregation_eligible=True),
            episode(
                episode_id="diagnostic",
                outcome="improved",
                aggregation_eligible=False,
                provenance=[{"kind": "experiment", "ref": "oracle-target-dependent"}],
            ),
        ]
        summary = aggregate_patterns(rows)
        key = "noisy_query::light_sonar_rephrase::v1"
        pattern = summary["patterns"][key]
        self.assertEqual(pattern["observed"], 4)
        self.assertEqual(pattern["eligible"], 3)
        self.assertEqual(pattern["outcomes"], {
            "improved": 1,
            "same": 0,
            "regressed": 1,
            "mixed": 0,
            "unknown": 1,
        })
        self.assertEqual(pattern["ineligible"], 1)
        self.assertEqual(summary["policy_candidates"], [])

    def test_jsonl_loader_validates_every_episode(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "episodes.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        episode(episode_id="a"),
                        episode(episode_id="b", outcome="same"),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            rows = load_jsonl(path)
        self.assertEqual([row["episode_id"] for row in rows], ["a", "b"])

    def test_jsonl_loader_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "episodes.jsonl"
            path.write_text(
                '{"schema":"theseus.session-search-retrieval-experience.v0",'
                '"episode_id":"a","episode_id":"b"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "DUPLICATE_KEY:episode_id"):
                load_jsonl(path)

    def test_jsonl_loader_rejects_duplicate_episode_id(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "episodes.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        episode(episode_id="same"),
                        episode(episode_id="same", outcome="regressed"),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "EXPERIENCE_DUPLICATE_ID:same"):
                load_jsonl(path)

    def test_seed_ledger_contains_positive_negative_and_unknown(self):
        rows = load_jsonl(
            ROOT
            / "experiments"
            / "015-retrieval-experience-ledger"
            / "seeds.public.jsonl"
        )
        outcomes = {row["outcome"] for row in rows}
        self.assertTrue({"improved", "regressed", "unknown"}.issubset(outcomes))
        self.assertTrue(all("query_text" not in row for row in rows))

    def test_processor_builds_deterministic_pattern_projection(self):
        source = (
            ROOT
            / "experiments"
            / "015-retrieval-experience-ledger"
            / "seeds.public.jsonl"
        )
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "patterns.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "process_retrieval_feedback.py"),
                    str(source),
                    str(out),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["episode_count"], 5)
        self.assertEqual(payload["policy_candidates"], [])
        self.assertEqual(payload["policy_promotion"], "NOT_PERFORMED")
        self.assertIn(
            "translation_morphology_drift::azure_notranslate_term_preservation::v3",
            payload["patterns"],
        )


if __name__ == "__main__":
    unittest.main()
