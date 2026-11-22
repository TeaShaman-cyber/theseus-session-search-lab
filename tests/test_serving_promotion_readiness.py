from __future__ import annotations

import hashlib
import contextlib
import io
import json
import pathlib
import tempfile
import subprocess
import unittest
from unittest import mock

from session_search.serving_promotion_readiness import (
    RECEIPT_SCHEMA,
    collect_repo_state,
    evaluate_serving_promotion_readiness,
    main,
    run_canonical_qa,
)


def generation_id(source_manifest_sha256: str) -> str:
    return hashlib.sha256(
        ("session-search-generation-v1\n" + source_manifest_sha256).encode("ascii")
    ).hexdigest()


def replacement(shard_id: str, source_seed: str, previous: str | None) -> dict:
    source_manifest = hashlib.sha256(source_seed.encode()).hexdigest()
    return {
        "provider": "chatgpt",
        "shard_id": shard_id,
        "generation_id": generation_id(source_manifest),
        "source_manifest_sha256": source_manifest,
        "bundle_sha256": hashlib.sha256(("bundle:" + shard_id).encode()).hexdigest(),
        "accepted_set_sha256": "a" * 64,
        "session_count": 10,
        "message_count": 100,
        "time_min": 1000.0,
        "time_max": 2000.0 if shard_id.endswith("1") else 2500.0,
        "state": "READY",
        "previous_generation_id": previous,
        "source_verified": True,
        "bundle_readback_verified": True,
        "ready_readback_verified": True,
    }


def manifest() -> dict:
    return {
        "schema": "theseus.session-search-serving-promotion-readiness-input.v1",
        "source_commit": "b" * 40,
        "source_watermarks": [
            {
                "provider": "chatgpt",
                "observed_through": 2600.0,
                "status": "PROVIDER_REPORTED_NO_KNOWN_GAPS",
                "basis": "provider_export_manifest",
                "accepted_set_sha256": "a" * 64,
            }
        ],
        "replacement_shards": [
            replacement("chatgpt-0001", "source-a", "c" * 64),
            replacement("chatgpt-0002", "source-b", "d" * 64),
        ],
        "unchanged_catalog": [
            {
                "provider": "deepseek",
                "shard_id": "deepseek-0001",
                "generation_id": "e" * 64,
                "time_min": 500.0,
                "time_max": 1500.0,
                "state": "READY",
            },
            {
                "provider": "xai",
                "shard_id": "xai-0001",
                "generation_id": "f" * 64,
                "time_min": 700.0,
                "time_max": 1700.0,
                "state": "READY",
            },
        ],
    }


def repo_state(*, clean: bool = True, head: str = "b" * 40) -> dict:
    return {"head": head, "branch": "ops/issue309", "clean": clean}


def qa_state(status: str = "PASS") -> dict:
    return {
        "status": status,
        "exit_code": 0 if status == "PASS" else 1,
        "output_sha256": "9" * 64,
    }


class ServingPromotionReadinessTest(unittest.TestCase):
    def test_valid_manifest_emits_compact_ready_receipt(self) -> None:
        receipt = evaluate_serving_promotion_readiness(
            manifest(),
            repo_state=repo_state(),
            qa_state=qa_state(),
        )

        self.assertEqual(receipt["schema"], RECEIPT_SCHEMA)
        self.assertEqual(receipt["state"], "READY_FOR_RUNTIME_GATES")
        self.assertEqual(receipt["blockers"], [])
        self.assertEqual(
            receipt["runtime_gates"],
            [
                "CANDIDATE_SHARD_AUTH_POSITIVE_NEGATIVE",
                "CANDIDATE_ROUTER_FANOUT_ZERO_UNAVAILABLE",
                "PRODUCTION_SAME_URI_RUNTIME_RELOAD",
                "ARCADE_WORKER_REFRESH",
                "APP_BACKED_COVERAGE_SEARCH_ACCEPTANCE",
            ],
        )
        self.assertEqual(
            [item["shard_id"] for item in receipt["replacement_shards"]],
            ["chatgpt-0001", "chatgpt-0002"],
        )
        self.assertEqual(receipt["rollback_generations"]["chatgpt-0001"], "c" * 64)
        self.assertEqual(
            [item["promotion_mode"] for item in receipt["replacement_shards"]],
            ["REPLACEMENT", "REPLACEMENT"],
        )
        self.assertEqual(receipt["qa"]["status"], "PASS")
        self.assertLess(
            len(json.dumps(receipt, separators=(",", ":"), sort_keys=True).encode()),
            8192,
        )


    def test_new_shard_allows_null_previous_generation_and_preserves_null_rollback(self) -> None:
        raw = manifest()
        raw["replacement_shards"] = [
            replacement("chatgpt-0003", "source-new", None)
        ]

        receipt = evaluate_serving_promotion_readiness(
            raw, repo_state=repo_state(), qa_state=qa_state()
        )

        self.assertEqual(receipt["state"], "READY_FOR_RUNTIME_GATES")
        self.assertEqual(receipt["blockers"], [])
        self.assertEqual(
            receipt["replacement_shards"][0]["promotion_mode"],
            "NEW_SHARD",
        )
        self.assertIsNone(receipt["rollback_generations"]["chatgpt-0003"])

    def test_new_shard_null_previous_blocks_if_shard_already_exists_in_unchanged_catalog(self) -> None:
        raw = manifest()
        raw["replacement_shards"] = [
            replacement("chatgpt-0003", "source-new", None)
        ]
        raw["unchanged_catalog"].append(
            {
                "provider": "chatgpt",
                "shard_id": "chatgpt-0003",
                "generation_id": "7" * 64,
                "time_min": 800.0,
                "time_max": 1800.0,
                "state": "READY",
            }
        )

        receipt = evaluate_serving_promotion_readiness(
            raw, repo_state=repo_state(), qa_state=qa_state()
        )

        self.assertEqual(receipt["state"], "BLOCKED")
        self.assertIn(
            "NEW_SHARD_ALREADY_EXISTS:chatgpt-0003",
            receipt["blockers"],
        )

    def test_replacement_still_rejects_invalid_non_null_previous_generation(self) -> None:
        raw = manifest()
        raw["replacement_shards"][0]["previous_generation_id"] = "not-a-generation"

        receipt = evaluate_serving_promotion_readiness(
            raw, repo_state=repo_state(), qa_state=qa_state()
        )

        self.assertEqual(receipt["state"], "BLOCKED")
        self.assertIn(
            "PREVIOUS_GENERATION_INVALID:chatgpt-0001",
            receipt["blockers"],
        )

    def test_generation_mismatch_blocks(self) -> None:
        raw = manifest()
        raw["replacement_shards"][0]["generation_id"] = "0" * 64

        receipt = evaluate_serving_promotion_readiness(
            raw, repo_state=repo_state(), qa_state=qa_state()
        )

        self.assertEqual(receipt["state"], "BLOCKED")
        self.assertIn("GENERATION_ID_MISMATCH:chatgpt-0001", receipt["blockers"])

    def test_missing_finite_bounds_blocks(self) -> None:
        raw = manifest()
        raw["replacement_shards"][0]["time_max"] = None

        receipt = evaluate_serving_promotion_readiness(
            raw, repo_state=repo_state(), qa_state=qa_state()
        )

        self.assertEqual(receipt["state"], "BLOCKED")
        self.assertIn("TIME_BOUNDS_INVALID:chatgpt-0001", receipt["blockers"])

    def test_dirty_worktree_and_qa_failure_block(self) -> None:
        receipt = evaluate_serving_promotion_readiness(
            manifest(),
            repo_state=repo_state(clean=False),
            qa_state=qa_state("FAIL"),
        )

        self.assertEqual(receipt["state"], "BLOCKED")
        self.assertIn("WORKTREE_DIRTY", receipt["blockers"])
        self.assertIn("CANONICAL_QA_NOT_PASS", receipt["blockers"])

    def test_source_commit_must_match_exact_head(self) -> None:
        receipt = evaluate_serving_promotion_readiness(
            manifest(),
            repo_state=repo_state(head="1" * 40),
            qa_state=qa_state(),
        )

        self.assertEqual(receipt["state"], "BLOCKED")
        self.assertIn("SOURCE_COMMIT_HEAD_MISMATCH", receipt["blockers"])

    def test_secret_like_field_fails_closed_without_echoing_value(self) -> None:
        raw = manifest()
        raw["replacement_shards"][0]["database_url"] = (
            "postgresql://user:super-secret@example.invalid/db"
        )

        receipt = evaluate_serving_promotion_readiness(
            raw, repo_state=repo_state(), qa_state=qa_state()
        )

        encoded = json.dumps(receipt, sort_keys=True)
        self.assertEqual(receipt["state"], "BLOCKED")
        self.assertIn(
            "FORBIDDEN_INPUT_KEY:replacement_shards[0].database_url",
            receipt["blockers"],
        )
        self.assertNotIn("super-secret", encoded)

    def test_watermark_must_cover_replacement_bounds_and_accepted_set(self) -> None:
        raw = manifest()
        raw["source_watermarks"][0]["observed_through"] = 2400.0
        raw["source_watermarks"][0]["accepted_set_sha256"] = "8" * 64

        receipt = evaluate_serving_promotion_readiness(
            raw, repo_state=repo_state(), qa_state=qa_state()
        )

        self.assertEqual(receipt["state"], "BLOCKED")
        self.assertIn("WATERMARK_BEFORE_DATA_MAX:chatgpt", receipt["blockers"])
        self.assertIn("ACCEPTED_SET_MISMATCH:chatgpt", receipt["blockers"])

    def test_duplicate_shard_ids_fail_closed(self) -> None:
        raw = manifest()
        raw["replacement_shards"][1]["shard_id"] = "chatgpt-0001"

        receipt = evaluate_serving_promotion_readiness(
            raw, repo_state=repo_state(), qa_state=qa_state()
        )

        self.assertEqual(receipt["state"], "BLOCKED")
        self.assertIn("DUPLICATE_SHARD_ID:chatgpt-0001", receipt["blockers"])

    def test_canonical_qa_receipt_is_bounded_and_hashes_full_output(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["./tools/dev/check"],
            returncode=0,
            stdout=b"line one\nDEV_CHECK_PASS\nprivate-noise-that-must-not-escape\n",
            stderr=b"",
        )
        with mock.patch(
            "session_search.serving_promotion_readiness.subprocess.run",
            return_value=completed,
        ) as run:
            result = run_canonical_qa()

        run.assert_called_once()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["exit_code"], 0)
        self.assertRegex(result["output_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("private-noise", json.dumps(result))

    def test_canonical_qa_requires_terminal_pass_marker(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["./tools/dev/check"],
            returncode=0,
            stdout=b"all commands happened but marker is absent\n",
            stderr=b"",
        )
        with mock.patch(
            "session_search.serving_promotion_readiness.subprocess.run",
            return_value=completed,
        ):
            result = run_canonical_qa()

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["exit_code"], 0)

    def test_collect_repo_state_reports_head_branch_and_cleanliness(self) -> None:
        responses = [
            subprocess.CompletedProcess(["git"], 0, stdout=b"b" * 40 + b"\n", stderr=b""),
            subprocess.CompletedProcess(["git"], 0, stdout=b"ops/issue309\n", stderr=b""),
            subprocess.CompletedProcess(["git"], 0, stdout=b"", stderr=b""),
        ]
        with mock.patch(
            "session_search.serving_promotion_readiness.subprocess.run",
            side_effect=responses,
        ) as run:
            result = collect_repo_state()

        self.assertEqual(run.call_count, 3)
        self.assertEqual(result, {"head": "b" * 40, "branch": "ops/issue309", "clean": True})

    def test_cli_run_qa_writes_atomic_compact_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            manifest_path = root / "manifest.json"
            output_path = root / "receipt.json"
            manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")

            with (
                mock.patch(
                    "session_search.serving_promotion_readiness.collect_repo_state",
                    return_value=repo_state(),
                ),
                mock.patch(
                    "session_search.serving_promotion_readiness.run_canonical_qa",
                    return_value=qa_state(),
                ) as qa,
                mock.patch(
                    "sys.argv",
                    [
                        "serving-promotion-readiness",
                        "--manifest",
                        str(manifest_path),
                        "--run-qa",
                        "--output",
                        str(output_path),
                    ],
                ),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
            ):
                rc = main()

            qa.assert_called_once()
            self.assertEqual(rc, 0)
            receipt = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["state"], "READY_FOR_RUNTIME_GATES")
            self.assertEqual(json.loads(stdout.getvalue())["state"], "READY_FOR_RUNTIME_GATES")
            self.assertLess(output_path.stat().st_size, 8192)

    def test_cli_without_run_qa_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")

            with (
                mock.patch(
                    "session_search.serving_promotion_readiness.collect_repo_state",
                    return_value=repo_state(),
                ),
                mock.patch(
                    "sys.argv",
                    ["serving-promotion-readiness", "--manifest", str(manifest_path)],
                ),
                contextlib.redirect_stdout(io.StringIO()) as stdout,
            ):
                rc = main()

            receipt = json.loads(stdout.getvalue())
            self.assertEqual(rc, 2)
            self.assertEqual(receipt["state"], "BLOCKED")
            self.assertIn("CANONICAL_QA_NOT_PASS", receipt["blockers"])


if __name__ == "__main__":
    unittest.main()
