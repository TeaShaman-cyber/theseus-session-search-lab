from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_SRC = ROOT / "arcade" / "session-search-materializer" / "src"
if str(PKG_SRC) not in sys.path:
    sys.path.insert(0, str(PKG_SRC))

from session_search_arcade_materializer.stage_ready import (  # noqa: E402
    StageToReadyError,
    stage_bundle_to_ready,
)


def materialized(status: str, target_state: str) -> dict:
    return {
        "schema": "theseus.session-search-shard-bundle.v1",
        "bundle_sha256": "a" * 64,
        "generation_id": "b" * 64,
        "source_manifest_sha256": "c" * 64,
        "provider": "chatgpt",
        "shard_id": "chatgpt-0001",
        "session_count": 12,
        "message_count": 345,
        "status": status,
        "target_state": target_state,
        "loaded_sessions": 12,
        "loaded_messages": 345,
        "neon_project_id": "project-a",
        "neon_branch_id": "br-project-a",
        "neon_database_name": "neondb",
        "loader_role": "session_search_arcade_loader_sql",
    }


def ready_receipt() -> dict:
    return {
        "status": "READY",
        "target_state": "READY",
        "generation_id": "b" * 64,
        "source_manifest_sha256": "c" * 64,
        "session_count": 12,
        "message_count": 345,
        "neon_project_id": "project-a",
        "neon_branch_id": "br-project-a",
        "neon_database_name": "neondb",
        "ready_role": "session_search_arcade_ready_sql",
    }


class StageBundleReadyTest(unittest.TestCase):
    def test_loaded_generation_is_marked_ready_once(self) -> None:
        calls = []

        async def materialize():
            return materialized("LOADED", "BUILDING")

        async def mark_ready(receipt):
            calls.append(receipt)
            return ready_receipt()

        result = asyncio.run(
            stage_bundle_to_ready(materialize=materialize, mark_ready=mark_ready)
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["generation_id"], "b" * 64)
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["target_state"], "READY")
        self.assertEqual(result["materialize_status"], "LOADED")
        self.assertTrue(result["ready_transition_performed"])
        self.assertEqual(result["generation_id"], "b" * 64)
        self.assertEqual(result["bundle_sha256"], "a" * 64)

    def test_existing_building_generation_is_marked_ready_once(self) -> None:
        calls = []

        async def materialize():
            return materialized("ALREADY_BUILDING", "BUILDING")

        async def mark_ready(receipt):
            calls.append(receipt)
            return ready_receipt()

        result = asyncio.run(
            stage_bundle_to_ready(materialize=materialize, mark_ready=mark_ready)
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["materialize_status"], "ALREADY_BUILDING")
        self.assertTrue(result["ready_transition_performed"])

    def test_existing_ready_generation_skips_ready_mutation(self) -> None:
        async def materialize():
            return materialized("ALREADY_READY", "READY")

        async def forbidden_mark_ready(_receipt):
            raise AssertionError("READY replay must not invoke READY mutation")

        result = asyncio.run(
            stage_bundle_to_ready(
                materialize=materialize,
                mark_ready=forbidden_mark_ready,
            )
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["materialize_status"], "ALREADY_READY")
        self.assertFalse(result["ready_transition_performed"])
        self.assertEqual(result["generation_id"], "b" * 64)

    def test_materialize_receipt_missing_identity_fails_closed(self) -> None:
        async def materialize():
            receipt = materialized("LOADED", "BUILDING")
            del receipt["source_manifest_sha256"]
            return receipt

        async def mark_ready(_receipt):
            raise AssertionError("invalid receipt must not be promoted")

        with self.assertRaisesRegex(StageToReadyError, "MATERIALIZE_RECEIPT_INCOMPLETE"):
            asyncio.run(
                stage_bundle_to_ready(materialize=materialize, mark_ready=mark_ready)
            )

    def test_unexpected_materialize_state_fails_closed(self) -> None:
        async def materialize():
            return materialized("LOADED", "READY")

        async def mark_ready(_receipt):
            raise AssertionError("state mismatch must not be promoted")

        with self.assertRaisesRegex(StageToReadyError, "MATERIALIZE_STATE_MISMATCH"):
            asyncio.run(
                stage_bundle_to_ready(materialize=materialize, mark_ready=mark_ready)
            )

    def test_unexpected_materialize_status_fails_closed(self) -> None:
        async def materialize():
            return materialized("UNKNOWN", "BUILDING")

        async def mark_ready(_receipt):
            raise AssertionError("unknown materialize status must not be promoted")

        with self.assertRaisesRegex(StageToReadyError, "MATERIALIZE_STATUS_UNEXPECTED"):
            asyncio.run(
                stage_bundle_to_ready(materialize=materialize, mark_ready=mark_ready)
            )

    def test_ready_receipt_identity_mismatch_fails_closed(self) -> None:
        async def materialize():
            return materialized("LOADED", "BUILDING")

        async def mark_ready(_receipt):
            receipt = ready_receipt()
            receipt["generation_id"] = "d" * 64
            return receipt

        with self.assertRaisesRegex(StageToReadyError, "READY_RECEIPT_MISMATCH"):
            asyncio.run(
                stage_bundle_to_ready(materialize=materialize, mark_ready=mark_ready)
            )

    def test_terminal_receipt_exposes_no_activation_or_catalog_fields(self) -> None:
        async def materialize():
            return materialized("ALREADY_READY", "READY")

        async def forbidden_mark_ready(_receipt):
            raise AssertionError

        result = asyncio.run(
            stage_bundle_to_ready(
                materialize=materialize,
                mark_ready=forbidden_mark_ready,
            )
        )

        self.assertNotIn("active_generation", result)
        self.assertNotIn("expected_previous_generation_id", result)
        self.assertNotIn("router_catalog", result)
        self.assertEqual(result["promotion_boundary"], "READY_ONLY_STOP")


if __name__ == "__main__":
    unittest.main()
