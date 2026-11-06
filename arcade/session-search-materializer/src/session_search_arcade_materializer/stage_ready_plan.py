from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any


STAGE_READY_PLAN_SCHEMA = "theseus.session-search-stage-ready-plan.v1"
STAGE_READY_RUN_SCHEMA = "theseus.session-search-stage-ready-run.v1"
STAGE_READY_RECEIPT_SCHEMA = "theseus.session-search-stage-ready.v1"

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SHARD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_DRIVE_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_PROJECT_ID = re.compile(r"^[a-z0-9-]{1,60}$")
_BRANCH_ID = re.compile(r"^br-[a-z0-9-]{1,60}$")
_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}$")

_PLAN_KEYS = {"schema", "source_commit", "parent_plan_digest", "shards"}
_SHARD_KEYS = {"shard_id", "inputs", "expected_receipt"}
_INPUT_KEYS = {
    "drive_file_id",
    "expected_bundle_sha256",
    "neon_project_id",
    "neon_branch_id",
    "neon_database_name",
}
_EXPECTED_RECEIPT_KEYS = {
    "bundle_sha256",
    "generation_id",
    "source_manifest_sha256",
    "provider",
    "shard_id",
    "session_count",
    "message_count",
    "neon_project_id",
    "neon_branch_id",
    "neon_database_name",
}
_RECEIPT_REQUIRED = _EXPECTED_RECEIPT_KEYS | {
    "schema",
    "status",
    "target_state",
    "promotion_boundary",
}


class StageReadyPlanError(ValueError):
    pass


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StageReadyPlanError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def _parse_json(raw: str) -> Any:
    if not isinstance(raw, str):
        raise StageReadyPlanError("PLAN_JSON_NOT_STRING")
    try:
        return json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except StageReadyPlanError:
        raise
    except json.JSONDecodeError as exc:
        raise StageReadyPlanError("PLAN_JSON_INVALID") from exc


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _exact_keys(value: Any, expected: set[str], *, code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise StageReadyPlanError(code)
    return value


def _string(value: Any, pattern: re.Pattern[str], *, code: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise StageReadyPlanError(code)
    return value


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise StageReadyPlanError("COUNT_OUT_OF_BOUNDS")
    return value


def _validate_shard(raw: Any) -> dict[str, Any]:
    shard = _exact_keys(raw, _SHARD_KEYS, code="SHARD_KEYS_MISMATCH")
    shard_id = _string(shard["shard_id"], _SHARD_ID, code="SHARD_ID_INVALID")
    inputs = _exact_keys(shard["inputs"], _INPUT_KEYS, code="INPUT_KEYS_MISMATCH")
    expected = _exact_keys(
        shard["expected_receipt"],
        _EXPECTED_RECEIPT_KEYS,
        code="EXPECTED_RECEIPT_KEYS_MISMATCH",
    )

    drive_file_id = _string(
        inputs["drive_file_id"], _DRIVE_FILE_ID, code="DRIVE_FILE_ID_INVALID"
    )
    bundle_sha256 = _string(
        inputs["expected_bundle_sha256"], _HEX64, code="BUNDLE_SHA256_INVALID"
    )
    project_id = _string(
        inputs["neon_project_id"], _PROJECT_ID, code="NEON_PROJECT_ID_INVALID"
    )
    branch_id = _string(
        inputs["neon_branch_id"], _BRANCH_ID, code="NEON_BRANCH_ID_INVALID"
    )
    database_name = _string(
        inputs["neon_database_name"], _DATABASE_NAME, code="NEON_DATABASE_NAME_INVALID"
    )

    normalized_expected = {
        "bundle_sha256": _string(
            expected["bundle_sha256"], _HEX64, code="BUNDLE_SHA256_INVALID"
        ),
        "generation_id": _string(
            expected["generation_id"], _HEX64, code="GENERATION_ID_INVALID"
        ),
        "source_manifest_sha256": _string(
            expected["source_manifest_sha256"],
            _HEX64,
            code="SOURCE_MANIFEST_SHA256_INVALID",
        ),
        "provider": _string(expected["provider"], _PROVIDER, code="PROVIDER_INVALID"),
        "shard_id": _string(expected["shard_id"], _SHARD_ID, code="SHARD_ID_INVALID"),
        "session_count": _positive_int(expected["session_count"]),
        "message_count": _positive_int(expected["message_count"]),
        "neon_project_id": _string(
            expected["neon_project_id"], _PROJECT_ID, code="NEON_PROJECT_ID_INVALID"
        ),
        "neon_branch_id": _string(
            expected["neon_branch_id"], _BRANCH_ID, code="NEON_BRANCH_ID_INVALID"
        ),
        "neon_database_name": _string(
            expected["neon_database_name"],
            _DATABASE_NAME,
            code="NEON_DATABASE_NAME_INVALID",
        ),
    }

    if (
        shard_id != normalized_expected["shard_id"]
        or bundle_sha256 != normalized_expected["bundle_sha256"]
        or project_id != normalized_expected["neon_project_id"]
        or branch_id != normalized_expected["neon_branch_id"]
        or database_name != normalized_expected["neon_database_name"]
    ):
        raise StageReadyPlanError("PLAN_IDENTITY_MISMATCH")

    return {
        "shard_id": shard_id,
        "inputs": {
            "drive_file_id": drive_file_id,
            "expected_bundle_sha256": bundle_sha256,
            "neon_project_id": project_id,
            "neon_branch_id": branch_id,
            "neon_database_name": database_name,
        },
        "expected_receipt": normalized_expected,
    }


def _validate_plan(
    raw: Any,
    *,
    expected_stage_ready_plan_digest: str,
) -> dict[str, Any]:
    plan = _exact_keys(raw, _PLAN_KEYS, code="PLAN_KEYS_MISMATCH")
    if plan["schema"] != STAGE_READY_PLAN_SCHEMA:
        raise StageReadyPlanError("PLAN_SCHEMA_UNSUPPORTED")
    source_commit = _string(
        plan["source_commit"], _HEX40, code="SOURCE_COMMIT_INVALID"
    )
    parent_plan_digest = _string(
        plan["parent_plan_digest"], _HEX64, code="PARENT_PLAN_DIGEST_INVALID"
    )
    expected_digest = _string(
        expected_stage_ready_plan_digest,
        _HEX64,
        code="EXPECTED_PLAN_DIGEST_INVALID",
    )
    actual_digest = _canonical_digest(plan)
    if not hmac.compare_digest(actual_digest, expected_digest):
        raise StageReadyPlanError("PLAN_DIGEST_MISMATCH")

    raw_shards = plan["shards"]
    if not isinstance(raw_shards, list) or not raw_shards:
        raise StageReadyPlanError("SHARDS_EMPTY")
    shards = [_validate_shard(item) for item in raw_shards]

    shard_ids = [item["shard_id"] for item in shards]
    if shard_ids != sorted(shard_ids):
        raise StageReadyPlanError("SHARDS_NOT_SORTED")
    if len(set(shard_ids)) != len(shard_ids):
        raise StageReadyPlanError("DUPLICATE_SHARD_ID")

    targets = [
        (item["inputs"]["neon_project_id"], item["inputs"]["neon_branch_id"])
        for item in shards
    ]
    if len(set(targets)) != len(targets):
        raise StageReadyPlanError("DUPLICATE_NEON_TARGET")

    return {
        "schema": STAGE_READY_PLAN_SCHEMA,
        "source_commit": source_commit,
        "parent_plan_digest": parent_plan_digest,
        "shards": shards,
        "stage_ready_plan_digest": actual_digest,
    }


def _validate_stage_receipt(receipt: Any, expected: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, dict) or not _RECEIPT_REQUIRED.issubset(receipt):
        raise StageReadyPlanError("STAGE_RECEIPT_INCOMPLETE")
    if (
        receipt.get("schema") != STAGE_READY_RECEIPT_SCHEMA
        or receipt.get("status") != "READY"
        or receipt.get("target_state") != "READY"
        or receipt.get("promotion_boundary") != "READY_ONLY_STOP"
    ):
        raise StageReadyPlanError("STAGE_RECEIPT_STATE_MISMATCH")
    if any(receipt.get(key) != expected[key] for key in _EXPECTED_RECEIPT_KEYS):
        raise StageReadyPlanError("STAGE_RECEIPT_MISMATCH")
    return receipt


async def execute_stage_ready_plan(
    *,
    stage_ready_plan_json: str,
    expected_stage_ready_plan_digest: str,
    stage_one: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    plan = _validate_plan(
        _parse_json(stage_ready_plan_json),
        expected_stage_ready_plan_digest=expected_stage_ready_plan_digest,
    )

    receipts: list[dict[str, Any]] = []
    for shard in plan["shards"]:
        receipt = await stage_one(dict(shard["inputs"]))
        receipts.append(
            _validate_stage_receipt(receipt, shard["expected_receipt"])
        )

    return {
        "schema": STAGE_READY_RUN_SCHEMA,
        "status": "READY",
        "target_state": "READY",
        "promotion_boundary": "READY_ONLY_STOP",
        "stage_ready_plan_digest": plan["stage_ready_plan_digest"],
        "parent_plan_digest": plan["parent_plan_digest"],
        "source_commit": plan["source_commit"],
        "shard_count": len(receipts),
        "shards": receipts,
    }
