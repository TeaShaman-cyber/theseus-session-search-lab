from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit


PLAN_SCHEMA = "theseus.session-search-refresh-plan.v1"
PREPARED_SCHEMA = "theseus.session-search-refresh-prepared.v1"

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_SHARD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_DRIVE_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_PROJECT_ID = re.compile(r"^[a-z0-9-]{1,60}$")
_BRANCH_ID = re.compile(r"^br-[a-z0-9-]{1,60}$")
_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}$")

_TOP_KEYS = {"schema", "source_commit", "shards"}
_SHARD_KEYS = {
    "provider",
    "shard_id",
    "drive_file_id",
    "expected_bundle_sha256",
    "neon_project_id",
    "neon_branch_id",
    "neon_database_name",
    "generation_id",
    "source_manifest_sha256",
    "expected_session_count",
    "expected_message_count",
    "expected_previous_generation_id",
    "serving_url",
}


class RefreshPlanError(ValueError):
    pass


def _generation_id(source_manifest_sha256: str) -> str:
    return hashlib.sha256(
        ("session-search-generation-v1\n" + source_manifest_sha256).encode("ascii")
    ).hexdigest()


def _require_exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise RefreshPlanError(f"{label}_KEYS_MISMATCH")


def _require_string(value: Any, pattern: re.Pattern[str], *, code: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise RefreshPlanError(code)
    return value


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RefreshPlanError("COUNT_OUT_OF_BOUNDS")
    return value


def _serving_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise RefreshPlanError("SERVING_URL_INVALID")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        raise RefreshPlanError("SERVING_URL_INVALID")
    return value


def _normalize_shard(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RefreshPlanError("SHARD_NOT_OBJECT")
    _require_exact_keys(raw, _SHARD_KEYS, label="SHARD")

    provider = _require_string(raw["provider"], _PROVIDER, code="PROVIDER_INVALID")
    shard_id = _require_string(raw["shard_id"], _SHARD_ID, code="SHARD_ID_INVALID")
    drive_file_id = _require_string(
        raw["drive_file_id"], _DRIVE_FILE_ID, code="DRIVE_FILE_ID_INVALID"
    )
    expected_bundle_sha256 = _require_string(
        raw["expected_bundle_sha256"], _HEX64, code="BUNDLE_SHA256_INVALID"
    )
    neon_project_id = _require_string(
        raw["neon_project_id"], _PROJECT_ID, code="NEON_PROJECT_ID_INVALID"
    )
    neon_branch_id = _require_string(
        raw["neon_branch_id"], _BRANCH_ID, code="NEON_BRANCH_ID_INVALID"
    )
    neon_database_name = _require_string(
        raw["neon_database_name"], _DATABASE_NAME, code="NEON_DATABASE_NAME_INVALID"
    )
    source_manifest_sha256 = _require_string(
        raw["source_manifest_sha256"], _HEX64, code="SOURCE_MANIFEST_SHA256_INVALID"
    )
    generation_id = _require_string(
        raw["generation_id"], _HEX64, code="GENERATION_ID_INVALID"
    )
    if generation_id != _generation_id(source_manifest_sha256):
        raise RefreshPlanError("GENERATION_ID_MISMATCH")

    expected_previous = raw["expected_previous_generation_id"]
    if expected_previous is not None:
        expected_previous = _require_string(
            expected_previous, _HEX64, code="EXPECTED_PREVIOUS_GENERATION_ID_INVALID"
        )
        if expected_previous == generation_id:
            raise RefreshPlanError("PREVIOUS_GENERATION_MATCHES_TARGET")

    return {
        "provider": provider,
        "shard_id": shard_id,
        "drive_file_id": drive_file_id,
        "expected_bundle_sha256": expected_bundle_sha256,
        "neon_project_id": neon_project_id,
        "neon_branch_id": neon_branch_id,
        "neon_database_name": neon_database_name,
        "generation_id": generation_id,
        "source_manifest_sha256": source_manifest_sha256,
        "expected_session_count": _positive_int(raw["expected_session_count"]),
        "expected_message_count": _positive_int(raw["expected_message_count"]),
        "expected_previous_generation_id": expected_previous,
        "serving_url": _serving_url(raw["serving_url"]),
    }


def normalize_refresh_manifest(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RefreshPlanError("PLAN_NOT_OBJECT")
    _require_exact_keys(raw, _TOP_KEYS, label="PLAN")
    if raw["schema"] != PLAN_SCHEMA:
        raise RefreshPlanError("PLAN_SCHEMA_UNSUPPORTED")

    source_commit = _require_string(
        raw["source_commit"], _HEX40, code="SOURCE_COMMIT_INVALID"
    )
    shards_raw = raw["shards"]
    if not isinstance(shards_raw, list) or not shards_raw:
        raise RefreshPlanError("SHARDS_EMPTY")

    shards = [_normalize_shard(value) for value in shards_raw]
    shards.sort(key=lambda item: item["shard_id"])

    seen_shards: set[str] = set()
    seen_targets: set[tuple[str, str]] = set()
    for item in shards:
        if item["shard_id"] in seen_shards:
            raise RefreshPlanError("DUPLICATE_SHARD_ID")
        seen_shards.add(item["shard_id"])

        target = (item["neon_project_id"], item["neon_branch_id"])
        if target in seen_targets:
            raise RefreshPlanError("DUPLICATE_NEON_TARGET")
        seen_targets.add(target)

    return {
        "schema": PLAN_SCHEMA,
        "source_commit": source_commit,
        "shards": shards,
    }


def _canonical_digest(normalized: dict[str, Any]) -> str:
    payload = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _materialize_action(shard: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": "materialize_building",
        "shard_id": shard["shard_id"],
        "inputs": {
            "drive_file_id": shard["drive_file_id"],
            "expected_bundle_sha256": shard["expected_bundle_sha256"],
            "neon_project_id": shard["neon_project_id"],
            "neon_branch_id": shard["neon_branch_id"],
            "neon_database_name": shard["neon_database_name"],
        },
    }


def _ready_action(shard: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": "mark_ready",
        "shard_id": shard["shard_id"],
        "inputs": {
            "neon_project_id": shard["neon_project_id"],
            "neon_branch_id": shard["neon_branch_id"],
            "neon_database_name": shard["neon_database_name"],
            "generation_id": shard["generation_id"],
            "expected_source_manifest_sha256": shard["source_manifest_sha256"],
            "expected_session_count": shard["expected_session_count"],
            "expected_message_count": shard["expected_message_count"],
        },
    }


def _activate_action(shard: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": "activate_generation",
        "shard_id": shard["shard_id"],
        "inputs": {
            "neon_project_id": shard["neon_project_id"],
            "neon_branch_id": shard["neon_branch_id"],
            "neon_database_name": shard["neon_database_name"],
            "generation_id": shard["generation_id"],
            "expected_previous_generation_id": shard["expected_previous_generation_id"],
        },
    }


def prepare_refresh_plan(raw: Any) -> dict[str, Any]:
    normalized = normalize_refresh_manifest(raw)
    shards = normalized["shards"]

    actions = [
        *(_materialize_action(shard) for shard in shards),
        *(_ready_action(shard) for shard in shards),
        *(_activate_action(shard) for shard in shards),
        {
            "stage": "update_router_catalog",
            "updates": [
                {
                    "provider": shard["provider"],
                    "shard_id": shard["shard_id"],
                    "generation_id": shard["generation_id"],
                    "serving_url": shard["serving_url"],
                }
                for shard in shards
            ],
        },
        {
            "stage": "app_smoke",
            "search_modes": ["strict", "auto"],
            "expected_shards": [shard["shard_id"] for shard in shards],
        },
    ]

    return {
        "schema": PREPARED_SCHEMA,
        "source_commit": normalized["source_commit"],
        "plan_digest": _canonical_digest(normalized),
        "promotion_boundary": "EXPLICIT_APPLY_REQUIRED",
        "mutates_state": False,
        "actions": actions,
    }
