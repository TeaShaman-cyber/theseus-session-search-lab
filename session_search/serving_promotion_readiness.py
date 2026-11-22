from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import subprocess
import tempfile
from typing import Any


INPUT_SCHEMA = "theseus.session-search-serving-promotion-readiness-input.v1"
RECEIPT_SCHEMA = "theseus.session-search-serving-promotion-readiness-receipt.v1"

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ROOT = pathlib.Path(__file__).resolve().parents[1]

_TOP_KEYS = {"schema", "source_commit", "source_watermarks", "replacement_shards", "unchanged_catalog"}
_WATERMARK_KEYS = {"provider", "observed_through", "status", "basis", "accepted_set_sha256"}
_REPLACEMENT_KEYS = {
    "provider", "shard_id", "generation_id", "source_manifest_sha256",
    "bundle_sha256", "accepted_set_sha256", "session_count", "message_count",
    "time_min", "time_max", "state", "previous_generation_id",
    "source_verified", "bundle_readback_verified", "ready_readback_verified",
}
_UNCHANGED_KEYS = {"provider", "shard_id", "generation_id", "time_min", "time_max", "state"}
_FORBIDDEN_KEY_TERMS = (
    "password", "token", "secret", "authorization", "database_url",
    "drive_file_id", "connection_string", "bearer", "credential", "oauth",
)
_RUNTIME_GATES = [
    "CANDIDATE_SHARD_AUTH_POSITIVE_NEGATIVE",
    "CANDIDATE_ROUTER_FANOUT_ZERO_UNAVAILABLE",
    "PRODUCTION_SAME_URI_RUNTIME_RELOAD",
    "ARCADE_WORKER_REFRESH",
    "APP_BACKED_COVERAGE_SEARCH_ACCEPTANCE",
]


def _generation_id(source_manifest_sha256: str) -> str:
    payload = "session-search-generation-v1\n" + source_manifest_sha256
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _is_hex(value: Any, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and bool(_NAME.fullmatch(value))


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _bounds_valid(item: dict[str, Any]) -> bool:
    lo, hi = item.get("time_min"), item.get("time_max")
    return _finite(lo) and _finite(hi) and float(lo) <= float(hi)


def _forbidden_paths(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if any(term in str(key).lower() for term in _FORBIDDEN_KEY_TERMS):
                found.append(child_path)
            else:
                found.extend(_forbidden_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_forbidden_paths(child, f"{path}[{index}]"))
    return found


def _run_bytes(command: list[str], *, cwd: pathlib.Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def collect_repo_state(*, root: pathlib.Path = _ROOT) -> dict[str, Any]:
    head = _run_bytes(["git", "rev-parse", "HEAD"], cwd=root)
    branch = _run_bytes(["git", "branch", "--show-current"], cwd=root)
    status = _run_bytes(["git", "status", "--porcelain"], cwd=root)
    if any(proc.returncode != 0 for proc in (head, branch, status)):
        return {"head": None, "branch": None, "clean": False}
    return {
        "head": head.stdout.decode("utf-8", errors="replace").strip(),
        "branch": branch.stdout.decode("utf-8", errors="replace").strip() or "(detached)",
        "clean": not bool(status.stdout.strip()),
    }


def run_canonical_qa(*, root: pathlib.Path = _ROOT) -> dict[str, Any]:
    completed = _run_bytes(["./tools/dev/check"], cwd=root)
    digest = hashlib.sha256(completed.stdout + b"\0STDERR\0" + completed.stderr).hexdigest()
    passed = completed.returncode == 0 and b"DEV_CHECK_PASS" in completed.stdout
    return {"status": "PASS" if passed else "FAIL", "exit_code": completed.returncode, "output_sha256": digest}


def _safe_qa(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    status = value.get("status")
    exit_code = value.get("exit_code")
    digest = value.get("output_sha256")
    return {
        "status": status if status in {"PASS", "FAIL", "NOT_RUN"} else "NOT_RUN",
        "exit_code": exit_code if isinstance(exit_code, int) else None,
        "output_sha256": digest if _is_hex(digest, _HEX64) else None,
    }


def _validate_watermarks(raw: Any, blockers: list[str]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    by_provider: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, list) or not raw:
        blockers.append("SOURCE_WATERMARKS_EMPTY")
        return output, by_provider

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            blockers.append(f"SOURCE_WATERMARK_NOT_OBJECT:{index}")
            continue
        if set(item) != _WATERMARK_KEYS:
            blockers.append(f"SOURCE_WATERMARK_KEYS_MISMATCH:{index}")
            continue
        provider = item["provider"]
        if not _is_name(provider):
            blockers.append(f"SOURCE_WATERMARK_PROVIDER_INVALID:{index}")
            continue
        if provider in by_provider:
            blockers.append(f"DUPLICATE_SOURCE_WATERMARK:{provider}")
            continue
        if not _finite(item["observed_through"]):
            blockers.append(f"SOURCE_WATERMARK_TIME_INVALID:{provider}")
        if item["status"] != "PROVIDER_REPORTED_NO_KNOWN_GAPS":
            blockers.append(f"SOURCE_WATERMARK_STATUS_INVALID:{provider}")
        if item["basis"] != "provider_export_manifest":
            blockers.append(f"SOURCE_WATERMARK_BASIS_INVALID:{provider}")
        if not _is_hex(item["accepted_set_sha256"], _HEX64):
            blockers.append(f"ACCEPTED_SET_INVALID:{provider}")
        normalized = dict(item)
        by_provider[provider] = normalized
        output.append(normalized)

    output.sort(key=lambda item: item["provider"])
    return output, by_provider


def _validate_replacements(
    raw: Any,
    *,
    blockers: list[str],
    seen_shards: set[str],
) -> tuple[
    list[dict[str, Any]],
    dict[str, str | None],
    dict[str, float],
    dict[str, set[str]],
    set[str],
]:
    output: list[dict[str, Any]] = []
    rollback: dict[str, str | None] = {}
    provider_max: dict[str, float] = {}
    accepted_sets: dict[str, set[str]] = {}
    new_shards: set[str] = set()

    if not isinstance(raw, list) or not raw:
        blockers.append("REPLACEMENT_SHARDS_EMPTY")
        return output, rollback, provider_max, accepted_sets, new_shards

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            blockers.append(f"REPLACEMENT_SHARD_NOT_OBJECT:{index}")
            continue
        if set(item) != _REPLACEMENT_KEYS:
            blockers.append(f"REPLACEMENT_KEYS_MISMATCH:{index}")
            continue

        provider, shard_id = item["provider"], item["shard_id"]
        if not _is_name(provider):
            blockers.append(f"PROVIDER_INVALID:{index}")
        if not _is_name(shard_id):
            blockers.append(f"SHARD_ID_INVALID:{index}")
            continue
        if shard_id in seen_shards:
            blockers.append(f"DUPLICATE_SHARD_ID:{shard_id}")
        seen_shards.add(shard_id)

        source_manifest = item["source_manifest_sha256"]
        generation = item["generation_id"]
        if not _is_hex(source_manifest, _HEX64):
            blockers.append(f"SOURCE_MANIFEST_INVALID:{shard_id}")
        if not _is_hex(generation, _HEX64):
            blockers.append(f"GENERATION_ID_INVALID:{shard_id}")
        elif _is_hex(source_manifest, _HEX64) and generation != _generation_id(source_manifest):
            blockers.append(f"GENERATION_ID_MISMATCH:{shard_id}")
        if not _is_hex(item["bundle_sha256"], _HEX64):
            blockers.append(f"BUNDLE_SHA256_INVALID:{shard_id}")
        if not _is_hex(item["accepted_set_sha256"], _HEX64):
            blockers.append(f"ACCEPTED_SET_INVALID:{shard_id}")
        previous_generation = item["previous_generation_id"]
        if previous_generation is None:
            promotion_mode = "NEW_SHARD"
            new_shards.add(shard_id)
        elif _is_hex(previous_generation, _HEX64):
            promotion_mode = "REPLACEMENT"
        else:
            promotion_mode = "REPLACEMENT"
            blockers.append(f"PREVIOUS_GENERATION_INVALID:{shard_id}")
        if item["state"] != "READY":
            blockers.append(f"GENERATION_NOT_READY:{shard_id}")
        if not _positive_int(item["session_count"]):
            blockers.append(f"SESSION_COUNT_INVALID:{shard_id}")
        if not _positive_int(item["message_count"]):
            blockers.append(f"MESSAGE_COUNT_INVALID:{shard_id}")
        if not _bounds_valid(item):
            blockers.append(f"TIME_BOUNDS_INVALID:{shard_id}")
        else:
            provider_max[provider] = max(provider_max.get(provider, float("-inf")), float(item["time_max"]))
        for field, code in (
            ("source_verified", "SOURCE_NOT_VERIFIED"),
            ("bundle_readback_verified", "BUNDLE_READBACK_NOT_VERIFIED"),
            ("ready_readback_verified", "READY_READBACK_NOT_VERIFIED"),
        ):
            if item[field] is not True:
                blockers.append(f"{code}:{shard_id}")

        accepted_sets.setdefault(provider, set()).add(item["accepted_set_sha256"])
        rollback[shard_id] = previous_generation
        normalized = {key: item[key] for key in (
            "provider", "shard_id", "generation_id", "source_manifest_sha256",
            "bundle_sha256", "accepted_set_sha256", "session_count",
            "message_count", "time_min", "time_max", "state",
        )}
        normalized["promotion_mode"] = promotion_mode
        output.append(normalized)

    output.sort(key=lambda item: item["shard_id"])
    return output, rollback, provider_max, accepted_sets, new_shards


def _validate_unchanged(
    raw: Any,
    *,
    blockers: list[str],
    seen_shards: set[str],
    new_shards: set[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        blockers.append("UNCHANGED_CATALOG_INVALID")
        return output

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            blockers.append(f"UNCHANGED_SHARD_NOT_OBJECT:{index}")
            continue
        if set(item) != _UNCHANGED_KEYS:
            blockers.append(f"UNCHANGED_KEYS_MISMATCH:{index}")
            continue
        provider, shard_id = item["provider"], item["shard_id"]
        if not _is_name(provider):
            blockers.append(f"UNCHANGED_PROVIDER_INVALID:{index}")
        if not _is_name(shard_id):
            blockers.append(f"UNCHANGED_SHARD_ID_INVALID:{index}")
            continue
        if shard_id in seen_shards:
            if shard_id in new_shards:
                blockers.append(f"NEW_SHARD_ALREADY_EXISTS:{shard_id}")
            else:
                blockers.append(f"DUPLICATE_SHARD_ID:{shard_id}")
        seen_shards.add(shard_id)
        if not _is_hex(item["generation_id"], _HEX64):
            blockers.append(f"UNCHANGED_GENERATION_INVALID:{shard_id}")
        if item["state"] != "READY":
            blockers.append(f"UNCHANGED_GENERATION_NOT_READY:{shard_id}")
        if not _bounds_valid(item):
            blockers.append(f"UNCHANGED_TIME_BOUNDS_INVALID:{shard_id}")
        output.append(dict(item))

    output.sort(key=lambda item: item["shard_id"])
    return output


def evaluate_serving_promotion_readiness(
    raw: Any,
    *,
    repo_state: dict[str, Any],
    qa_state: dict[str, Any] | None,
) -> dict[str, Any]:
    blockers = [f"FORBIDDEN_INPUT_KEY:{path}" for path in _forbidden_paths(raw)]

    head = repo_state.get("head") if isinstance(repo_state, dict) else None
    branch = repo_state.get("branch") if isinstance(repo_state, dict) else None
    clean = bool(repo_state.get("clean")) if isinstance(repo_state, dict) else False
    if not _is_hex(head, _HEX40):
        blockers.append("REPO_HEAD_INVALID")
    if not isinstance(branch, str) or not branch:
        blockers.append("REPO_BRANCH_INVALID")
    if not clean:
        blockers.append("WORKTREE_DIRTY")

    qa = _safe_qa(qa_state)
    if qa["status"] != "PASS" or qa["exit_code"] != 0 or qa["output_sha256"] is None:
        blockers.append("CANONICAL_QA_NOT_PASS")

    source_commit: str | None = None
    watermarks: list[dict[str, Any]] = []
    replacements: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    rollback: dict[str, str | None] = {}

    if not isinstance(raw, dict):
        blockers.append("INPUT_NOT_OBJECT")
    else:
        if set(raw) != _TOP_KEYS:
            blockers.append("INPUT_KEYS_MISMATCH")
        if raw.get("schema") != INPUT_SCHEMA:
            blockers.append("INPUT_SCHEMA_UNSUPPORTED")

        candidate_commit = raw.get("source_commit")
        if _is_hex(candidate_commit, _HEX40):
            source_commit = candidate_commit
            if _is_hex(head, _HEX40) and candidate_commit != head:
                blockers.append("SOURCE_COMMIT_HEAD_MISMATCH")
        else:
            blockers.append("SOURCE_COMMIT_INVALID")

        watermarks, watermark_by_provider = _validate_watermarks(raw.get("source_watermarks"), blockers)
        seen_shards: set[str] = set()
        replacements, rollback, provider_max, accepted_sets, new_shards = _validate_replacements(
            raw.get("replacement_shards"), blockers=blockers, seen_shards=seen_shards
        )
        unchanged = _validate_unchanged(
            raw.get("unchanged_catalog"),
            blockers=blockers,
            seen_shards=seen_shards,
            new_shards=new_shards,
        )

        for provider, data_max in provider_max.items():
            watermark = watermark_by_provider.get(provider)
            if watermark is None:
                blockers.append(f"SOURCE_WATERMARK_MISSING:{provider}")
                continue
            if _finite(watermark["observed_through"]) and float(watermark["observed_through"]) < data_max:
                blockers.append(f"WATERMARK_BEFORE_DATA_MAX:{provider}")
            sets = accepted_sets.get(provider, set())
            if len(sets) != 1 or watermark["accepted_set_sha256"] not in sets:
                blockers.append(f"ACCEPTED_SET_MISMATCH:{provider}")

    blockers = list(dict.fromkeys(blockers))
    return {
        "schema": RECEIPT_SCHEMA,
        "state": "READY_FOR_RUNTIME_GATES" if not blockers else "BLOCKED",
        "source_commit": source_commit,
        "repo": {"head": head, "branch": branch, "clean": clean},
        "qa": qa,
        "source_watermarks": watermarks,
        "replacement_shards": replacements,
        "unchanged_catalog": unchanged,
        "rollback_generations": dict(sorted(rollback.items())),
        "runtime_gates": list(_RUNTIME_GATES),
        "blockers": blockers,
    }


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"DUPLICATE_JSON_KEY:{key}")
        result[key] = value
    return result


def _load_manifest(path: pathlib.Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=_object_no_duplicates)


def _input_error_receipt(
    *,
    repo_state: dict[str, Any],
    qa_state: dict[str, Any] | None,
    blocker: str,
) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "state": "BLOCKED",
        "source_commit": None,
        "repo": {
            "head": repo_state.get("head"),
            "branch": repo_state.get("branch"),
            "clean": bool(repo_state.get("clean")),
        },
        "qa": _safe_qa(qa_state),
        "source_watermarks": [],
        "replacement_shards": [],
        "unchanged_catalog": [],
        "rollback_generations": {},
        "runtime_gates": list(_RUNTIME_GATES),
        "blockers": [blocker],
    }


def _write_atomic(path: pathlib.Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emit one compact, read-only Session Search serving-promotion readiness receipt. This command never performs cutover."
    )
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--run-qa", action="store_true", help="Run canonical ./tools/dev/check once inside this bounded job.")
    parser.add_argument("--output", type=pathlib.Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    repo_state = collect_repo_state()
    qa_state = run_canonical_qa() if args.run_qa else {
        "status": "NOT_RUN", "exit_code": None, "output_sha256": None
    }

    try:
        raw = _load_manifest(args.manifest)
    except FileNotFoundError:
        receipt = _input_error_receipt(repo_state=repo_state, qa_state=qa_state, blocker="MANIFEST_NOT_FOUND")
    except (json.JSONDecodeError, UnicodeDecodeError):
        receipt = _input_error_receipt(repo_state=repo_state, qa_state=qa_state, blocker="MANIFEST_JSON_INVALID")
    except ValueError as exc:
        code = str(exc)
        receipt = _input_error_receipt(
            repo_state=repo_state,
            qa_state=qa_state,
            blocker=code if code.startswith("DUPLICATE_JSON_KEY:") else "MANIFEST_INVALID",
        )
    else:
        receipt = evaluate_serving_promotion_readiness(raw, repo_state=repo_state, qa_state=qa_state)

    serialized = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if args.output is not None:
        _write_atomic(args.output, serialized)
    print(serialized)
    return 0 if receipt["state"] == "READY_FOR_RUNTIME_GATES" else 2


if __name__ == "__main__":
    raise SystemExit(main())
