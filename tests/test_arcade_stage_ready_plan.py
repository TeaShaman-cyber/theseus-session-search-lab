from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_SRC = ROOT / "arcade" / "session-search-materializer" / "src"
if str(PKG_SRC) not in sys.path:
    sys.path.insert(0, str(PKG_SRC))

from session_search_arcade_materializer.stage_ready_plan import (  # noqa: E402
    StageReadyPlanError,
    execute_stage_ready_plan,
)


PLAN_SCHEMA = "theseus.session-search-stage-ready-plan.v1"


def canonical_digest(value: dict) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def shard(n: int) -> dict:
    sid = f"chatgpt-{n:04d}"
    bundle = hashlib.sha256(f"bundle-{n}".encode()).hexdigest()
    generation = hashlib.sha256(f"generation-{n}".encode()).hexdigest()
    source = hashlib.sha256(f"source-{n}".encode()).hexdigest()
    project = f"project-{n}"
    branch = f"br-project-{n}"
    return {
        "shard_id": sid,
        "inputs": {
            "drive_file_id": f"drive_{sid.replace('-', '_')}_12345",
            "expected_bundle_sha256": bundle,
            "neon_project_id": project,
            "neon_branch_id": branch,
            "neon_database_name": "neondb",
        },
        "expected_receipt": {
            "bundle_sha256": bundle,
            "generation_id": generation,
            "source_manifest_sha256": source,
            "provider": "chatgpt",
            "shard_id": sid,
            "session_count": n * 10,
            "message_count": n * 100,
            "neon_project_id": project,
            "neon_branch_id": branch,
            "neon_database_name": "neondb",
        },
    }


def plan() -> dict:
    return {
        "schema": PLAN_SCHEMA,
        "source_commit": "a" * 40,
        "parent_plan_digest": "b" * 64,
        "shards": [shard(1), shard(2)],
    }


def ready_receipt(entry: dict) -> dict:
    expected = entry["expected_receipt"]
    return {
        "schema": "theseus.session-search-stage-ready.v1",
        "status": "READY",
        "target_state": "READY",
        "promotion_boundary": "READY_ONLY_STOP",
        "materialize_status": "LOADED",
        "ready_transition_performed": True,
        **expected,
    }


class StageReadyPlanExecutorTest(unittest.TestCase):
    def test_valid_plan_executes_sequentially_and_returns_terminal_receipt(self) -> None:
        p = plan()
        digest = canonical_digest(p)
        calls = []

        async def stage_one(inputs):
            calls.append(inputs["drive_file_id"])
            entry = p["shards"][len(calls) - 1]
            return ready_receipt(entry)

        result = asyncio.run(
            execute_stage_ready_plan(
                stage_ready_plan_json=json.dumps(p),
                expected_stage_ready_plan_digest=digest,
                stage_one=stage_one,
            )
        )

        self.assertEqual(
            calls,
            [
                "drive_chatgpt_0001_12345",
                "drive_chatgpt_0002_12345",
            ],
        )
        self.assertEqual(result["schema"], "theseus.session-search-stage-ready-run.v1")
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["target_state"], "READY")
        self.assertEqual(result["promotion_boundary"], "READY_ONLY_STOP")
        self.assertEqual(result["stage_ready_plan_digest"], digest)
        self.assertEqual(result["parent_plan_digest"], "b" * 64)
        self.assertEqual(result["source_commit"], "a" * 40)
        self.assertEqual(result["shard_count"], 2)
        self.assertEqual(len(result["shards"]), 2)

    def test_digest_mismatch_fails_before_any_stage(self) -> None:
        calls = []

        async def forbidden(_inputs):
            calls.append("called")
            raise AssertionError

        with self.assertRaisesRegex(StageReadyPlanError, "PLAN_DIGEST_MISMATCH"):
            asyncio.run(
                execute_stage_ready_plan(
                    stage_ready_plan_json=json.dumps(plan()),
                    expected_stage_ready_plan_digest="0" * 64,
                    stage_one=forbidden,
                )
            )
        self.assertEqual(calls, [])

    def test_duplicate_json_key_fails_closed(self) -> None:
        raw = json.dumps(plan()).replace(
            '"schema": "' + PLAN_SCHEMA + '"',
            '"schema": "' + PLAN_SCHEMA + '", "schema": "' + PLAN_SCHEMA + '"',
            1,
        )

        async def forbidden(_inputs):
            raise AssertionError

        with self.assertRaisesRegex(StageReadyPlanError, "DUPLICATE_JSON_KEY:schema"):
            asyncio.run(
                execute_stage_ready_plan(
                    stage_ready_plan_json=raw,
                    expected_stage_ready_plan_digest="0" * 64,
                    stage_one=forbidden,
                )
            )

    def test_unknown_or_promotion_fields_fail_closed(self) -> None:
        for field, value in [
            ("activate_generation", {}),
            ("expected_previous_generation_id", "c" * 64),
            ("serving_url", "https://example.test/search"),
        ]:
            with self.subTest(field=field):
                p = plan()
                p[field] = value

                async def forbidden(_inputs):
                    raise AssertionError

                with self.assertRaisesRegex(StageReadyPlanError, "PLAN_KEYS_MISMATCH"):
                    asyncio.run(
                        execute_stage_ready_plan(
                            stage_ready_plan_json=json.dumps(p),
                            expected_stage_ready_plan_digest=canonical_digest(p),
                            stage_one=forbidden,
                        )
                    )

    def test_duplicate_shard_or_target_fails_closed(self) -> None:
        cases = []

        dup_shard = plan()
        dup_shard["shards"][1]["shard_id"] = dup_shard["shards"][0]["shard_id"]
        dup_shard["shards"][1]["expected_receipt"]["shard_id"] = dup_shard["shards"][0]["shard_id"]
        cases.append((dup_shard, "DUPLICATE_SHARD_ID"))

        dup_target = plan()
        dup_target["shards"][1]["inputs"]["neon_project_id"] = dup_target["shards"][0]["inputs"]["neon_project_id"]
        dup_target["shards"][1]["inputs"]["neon_branch_id"] = dup_target["shards"][0]["inputs"]["neon_branch_id"]
        dup_target["shards"][1]["expected_receipt"]["neon_project_id"] = dup_target["shards"][0]["inputs"]["neon_project_id"]
        dup_target["shards"][1]["expected_receipt"]["neon_branch_id"] = dup_target["shards"][0]["inputs"]["neon_branch_id"]
        cases.append((dup_target, "DUPLICATE_NEON_TARGET"))

        for p, code in cases:
            with self.subTest(code=code):
                async def forbidden(_inputs):
                    raise AssertionError

                with self.assertRaisesRegex(StageReadyPlanError, code):
                    asyncio.run(
                        execute_stage_ready_plan(
                            stage_ready_plan_json=json.dumps(p),
                            expected_stage_ready_plan_digest=canonical_digest(p),
                            stage_one=forbidden,
                        )
                    )

    def test_internal_identity_mismatch_fails_closed(self) -> None:
        p = plan()
        p["shards"][0]["expected_receipt"]["bundle_sha256"] = "f" * 64

        async def forbidden(_inputs):
            raise AssertionError

        with self.assertRaisesRegex(StageReadyPlanError, "PLAN_IDENTITY_MISMATCH"):
            asyncio.run(
                execute_stage_ready_plan(
                    stage_ready_plan_json=json.dumps(p),
                    expected_stage_ready_plan_digest=canonical_digest(p),
                    stage_one=forbidden,
                )
            )

    def test_receipt_mismatch_stops_later_shards(self) -> None:
        p = plan()
        digest = canonical_digest(p)
        calls = []

        async def stage_one(_inputs):
            calls.append(len(calls))
            receipt = ready_receipt(p["shards"][0])
            receipt["generation_id"] = "f" * 64
            return receipt

        with self.assertRaisesRegex(StageReadyPlanError, "STAGE_RECEIPT_MISMATCH"):
            asyncio.run(
                execute_stage_ready_plan(
                    stage_ready_plan_json=json.dumps(p),
                    expected_stage_ready_plan_digest=digest,
                    stage_one=stage_one,
                )
            )
        self.assertEqual(len(calls), 1)

    def test_unsorted_shards_fail_closed(self) -> None:
        p = plan()
        p["shards"] = list(reversed(p["shards"]))

        async def forbidden(_inputs):
            raise AssertionError

        with self.assertRaisesRegex(StageReadyPlanError, "SHARDS_NOT_SORTED"):
            asyncio.run(
                execute_stage_ready_plan(
                    stage_ready_plan_json=json.dumps(p),
                    expected_stage_ready_plan_digest=canonical_digest(p),
                    stage_one=forbidden,
                )
            )


if __name__ == "__main__":
    unittest.main()
