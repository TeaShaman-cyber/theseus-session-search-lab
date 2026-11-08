import json
import pathlib
import stat
import tempfile
import unittest

from session_search.feedback_refresh import (
    build_feedback_refresh,
    write_feedback_refresh,
)
from session_search.retrieval_experience import feedback_ref_for_request


def feedback_request(seed: str, outcome: str = "improved"):
    return {
        "schema": "theseus.session-search-feedback-request.v0",
        "baseline_request_ref": seed * 32,
        "result_request_ref": None,
        "failure_class": "noisy_query",
        "operator": {"name": "light_sonar_rephrase", "version": "v1"},
        "outcome": outcome,
        "notes": "private operational note that must not reach experience projection",
    }


def feedback_record(seed: str, outcome: str = "improved"):
    request = feedback_request(seed, outcome)
    ref = feedback_ref_for_request(request)
    return {
        "schema": "theseus.session-search-feedback-record.v0",
        "feedback_ref": ref,
        "received_at": "2026-11-08T01:20:00.000Z",
        "feedback": request,
    }


def seed_episode():
    return {
        "schema": "theseus.session-search-retrieval-experience.v0",
        "episode_id": "seed-episode",
        "observed_at": "2026-11-01T00:00:00Z",
        "failure_class": "candidate_recall",
        "operator": {"name": "per_session_cap_relaxation", "version": "v1"},
        "outcome": "same",
        "confidence": "FACT",
        "aggregation_eligible": True,
        "baseline": {"status": "FOUND", "request_ref": "seed-baseline"},
        "result": {"status": "FOUND", "request_ref": "seed-result"},
        "provenance": [{"kind": "issue", "ref": "example#1"}],
    }


class FeedbackRefreshTest(unittest.TestCase):
    def test_build_combines_seed_and_live_without_notes_or_policy_promotion(self):
        payload = build_feedback_refresh(
            [feedback_record("a"), feedback_record("b", "regressed")],
            seed_episodes=[seed_episode()],
        )
        self.assertEqual(len(payload["episodes"]), 3)
        self.assertEqual(
            [row["episode_id"] for row in payload["episodes"]],
            sorted(row["episode_id"] for row in payload["episodes"]),
        )
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private operational note", encoded)
        self.assertEqual(payload["patterns"]["episode_count"], 3)
        self.assertEqual(payload["patterns"]["policy_candidates"], [])
        self.assertEqual(payload["patterns"]["policy_promotion"], "NOT_PERFORMED")
        self.assertEqual(payload["source_counts"], {"seed": 1, "live_feedback": 2})

    def test_build_rejects_duplicate_episode_identity_across_sources(self):
        live = feedback_record("a")
        seed = seed_episode()
        seed["episode_id"] = live["feedback_ref"]
        with self.assertRaisesRegex(ValueError, "FEEDBACK_REFRESH_DUPLICATE_EPISODE"):
            build_feedback_refresh([live], seed_episodes=[seed])

    def test_write_is_deterministic_atomic_and_private(self):
        payload = build_feedback_refresh(
            [feedback_record("a")],
            seed_episodes=[seed_episode()],
        )
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            episodes = root / "experience.jsonl"
            patterns = root / "patterns.json"
            write_feedback_refresh(payload, episodes_path=episodes, patterns_path=patterns)
            first_episodes = episodes.read_bytes()
            first_patterns = patterns.read_bytes()
            self.assertEqual(stat.S_IMODE(episodes.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(patterns.stat().st_mode), 0o600)
            self.assertFalse(list(root.glob("*.tmp")))
            write_feedback_refresh(payload, episodes_path=episodes, patterns_path=patterns)
            self.assertEqual(episodes.read_bytes(), first_episodes)
            self.assertEqual(patterns.read_bytes(), first_patterns)


if __name__ == "__main__":
    unittest.main()
