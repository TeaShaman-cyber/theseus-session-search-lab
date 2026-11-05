from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


STAGE_RECEIPT_SCHEMA = "theseus.session-search-stage-ready.v1"

_MATERIALIZE_REQUIRED = (
    "bundle_sha256",
    "generation_id",
    "source_manifest_sha256",
    "provider",
    "shard_id",
    "session_count",
    "message_count",
    "status",
    "target_state",
    "neon_project_id",
    "neon_branch_id",
    "neon_database_name",
)

_READY_IDENTITY_KEYS = (
    "generation_id",
    "source_manifest_sha256",
    "session_count",
    "message_count",
    "neon_project_id",
    "neon_branch_id",
    "neon_database_name",
)


class StageToReadyError(ValueError):
    pass


def _require_mapping(value: Any, *, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StageToReadyError(code)
    return value


def _require_materialize_receipt(value: Any) -> dict[str, Any]:
    receipt = _require_mapping(value, code="MATERIALIZE_RECEIPT_NOT_OBJECT")
    if any(key not in receipt for key in _MATERIALIZE_REQUIRED):
        raise StageToReadyError("MATERIALIZE_RECEIPT_INCOMPLETE")
    return receipt


def _terminal_receipt(
    materialized: dict[str, Any],
    *,
    ready_transition_performed: bool,
) -> dict[str, Any]:
    return {
        "schema": STAGE_RECEIPT_SCHEMA,
        "status": "READY",
        "target_state": "READY",
        "promotion_boundary": "READY_ONLY_STOP",
        "materialize_status": materialized["status"],
        "ready_transition_performed": ready_transition_performed,
        "bundle_sha256": materialized["bundle_sha256"],
        "generation_id": materialized["generation_id"],
        "source_manifest_sha256": materialized["source_manifest_sha256"],
        "provider": materialized["provider"],
        "shard_id": materialized["shard_id"],
        "session_count": materialized["session_count"],
        "message_count": materialized["message_count"],
        "neon_project_id": materialized["neon_project_id"],
        "neon_branch_id": materialized["neon_branch_id"],
        "neon_database_name": materialized["neon_database_name"],
    }


def _validate_ready_receipt(
    ready: Any,
    *,
    materialized: dict[str, Any],
) -> None:
    receipt = _require_mapping(ready, code="READY_RECEIPT_NOT_OBJECT")
    if receipt.get("status") != "READY" or receipt.get("target_state") != "READY":
        raise StageToReadyError("READY_RECEIPT_STATE_MISMATCH")
    mismatches = [
        key
        for key in _READY_IDENTITY_KEYS
        if receipt.get(key) != materialized.get(key)
    ]
    if mismatches:
        raise StageToReadyError("READY_RECEIPT_MISMATCH")


async def stage_bundle_to_ready(
    *,
    materialize: Callable[[], Awaitable[dict[str, Any]]],
    mark_ready: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    materialized = _require_materialize_receipt(await materialize())
    status = materialized["status"]
    target_state = materialized["target_state"]

    if status == "ALREADY_READY":
        if target_state != "READY":
            raise StageToReadyError("MATERIALIZE_STATE_MISMATCH")
        return _terminal_receipt(
            materialized,
            ready_transition_performed=False,
        )

    if status not in {"LOADED", "ALREADY_BUILDING"}:
        raise StageToReadyError("MATERIALIZE_STATUS_UNEXPECTED")
    if target_state != "BUILDING":
        raise StageToReadyError("MATERIALIZE_STATE_MISMATCH")

    ready = await mark_ready(materialized)
    _validate_ready_receipt(ready, materialized=materialized)

    return _terminal_receipt(
        materialized,
        ready_transition_performed=True,
    )
