from __future__ import annotations

import hashlib
import json
import pathlib
import re
from datetime import datetime
from collections import defaultdict
from typing import Any

from session_search.strict_json import StrictJsonError, loads_strict

FEEDBACK_REQUEST_SCHEMA = "theseus.session-search-feedback-request.v0"
FEEDBACK_RECORD_SCHEMA = "theseus.session-search-feedback-record.v0"
EXPERIENCE_SCHEMA = "theseus.session-search-retrieval-experience.v0"
PATTERN_SCHEMA = "theseus.session-search-retrieval-patterns.v0"

_OUTCOMES = ("improved", "same", "regressed", "mixed", "unknown")
_CONFIDENCE = {"FACT", "INFERENCE", "HYPOTHESIS", "UNKNOWN"}
_PRIVATE_TEXT_KEYS = {
    "query",
    "query_text",
    "raw_query",
    "target",
    "target_text",
    "raw_target",
}
_FEEDBACK_FIELDS = {
    "schema",
    "baseline_request_ref",
    "result_request_ref",
    "failure_class",
    "operator",
    "outcome",
    "notes",
}
_FEEDBACK_RECORD_FIELDS = {
    "schema",
    "feedback_ref",
    "received_at",
    "feedback",
}
_EXPERIENCE_FIELDS = {
    "schema",
    "episode_id",
    "observed_at",
    "failure_class",
    "operator",
    "outcome",
    "confidence",
    "aggregation_eligible",
    "baseline",
    "result",
    "provenance",
}
_OPERATOR_FIELDS = {"name", "version"}
_PROVENANCE_FIELDS = {"kind", "ref"}
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_REQUEST_REF_RE = re.compile(r"^[0-9a-f]{32}$")


def _require_mapping(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(code)
    return value


def _require_string(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(code)
    return value


def _require_slug(value: Any, code: str) -> str:
    value = _require_string(value, code)
    if not _SLUG_RE.fullmatch(value):
        raise ValueError(code)
    return value


def _require_request_ref(value: Any, code: str) -> str:
    value = _require_string(value, code)
    if not _REQUEST_REF_RE.fullmatch(value):
        raise ValueError(code)
    return value


def _require_no_unknown_fields(
    mapping: dict[str, Any],
    allowed: set[str],
    code: str,
) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise ValueError(f"{code}:{','.join(sorted(unknown))}")


def _forbid_private_text(value: Any, code: str) -> None:
    if isinstance(value, dict):
        if _PRIVATE_TEXT_KEYS.intersection(value):
            raise ValueError(code)
        for nested in value.values():
            _forbid_private_text(nested, code)
        return
    if isinstance(value, list):
        for nested in value:
            _forbid_private_text(nested, code)


def _validate_operator(value: Any) -> dict[str, Any]:
    operator = _require_mapping(value, "EXPERIENCE_OPERATOR_INVALID")
    _require_no_unknown_fields(
        operator,
        _OPERATOR_FIELDS,
        "EXPERIENCE_OPERATOR_FIELDS_INVALID",
    )
    _require_slug(operator.get("name"), "EXPERIENCE_OPERATOR_INVALID")
    _require_slug(operator.get("version"), "EXPERIENCE_OPERATOR_INVALID")
    return operator


def validate_feedback_request(value: dict[str, Any]) -> dict[str, Any]:
    value = _require_mapping(value, "FEEDBACK_REQUEST_INVALID")
    _forbid_private_text(value, "FEEDBACK_PRIVATE_TEXT_FORBIDDEN")
    _require_no_unknown_fields(value, _FEEDBACK_FIELDS, "FEEDBACK_FIELDS_INVALID")
    if value.get("schema") != FEEDBACK_REQUEST_SCHEMA:
        raise ValueError("FEEDBACK_SCHEMA_INVALID")

    _require_request_ref(
        value.get("baseline_request_ref"),
        "FEEDBACK_BASELINE_REF_INVALID",
    )
    result_ref = value.get("result_request_ref")
    if result_ref is not None:
        _require_request_ref(result_ref, "FEEDBACK_RESULT_REF_INVALID")
    _require_slug(value.get("failure_class"), "FEEDBACK_FAILURE_CLASS_INVALID")
    _validate_operator(value.get("operator"))

    outcome = value.get("outcome")
    if outcome not in _OUTCOMES:
        raise ValueError("FEEDBACK_OUTCOME_INVALID")

    notes = value.get("notes")
    if notes is not None:
        _require_string(notes, "FEEDBACK_NOTES_INVALID")
        if len(notes) > 4000:
            raise ValueError("FEEDBACK_NOTES_TOO_LONG")

    return value


def _canonical_feedback_request(value: dict[str, Any]) -> dict[str, Any]:
    validate_feedback_request(value)
    return {
        "schema": FEEDBACK_REQUEST_SCHEMA,
        "baseline_request_ref": value["baseline_request_ref"],
        "result_request_ref": value.get("result_request_ref"),
        "failure_class": value["failure_class"],
        "operator": {
            "name": value["operator"]["name"],
            "version": value["operator"]["version"],
        },
        "outcome": value["outcome"],
        "notes": value.get("notes"),
    }


def feedback_ref_for_request(value: dict[str, Any]) -> str:
    canonical = _canonical_feedback_request(value)
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256()
    digest.update(b"theseus.session-search-feedback-record.v0\n")
    digest.update(payload.encode("utf-8"))
    return digest.hexdigest()[:32]


def _require_iso_utc(value: Any, code: str) -> str:
    value = _require_string(value, code)
    if not value.endswith("Z"):
        raise ValueError(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(code) from exc
    if parsed.utcoffset() is None:
        raise ValueError(code)
    return value


def validate_feedback_record(value: dict[str, Any]) -> dict[str, Any]:
    value = _require_mapping(value, "FEEDBACK_RECORD_INVALID")
    _require_no_unknown_fields(
        value,
        _FEEDBACK_RECORD_FIELDS,
        "FEEDBACK_RECORD_FIELDS_INVALID",
    )
    if value.get("schema") != FEEDBACK_RECORD_SCHEMA:
        raise ValueError("FEEDBACK_RECORD_SCHEMA_INVALID")

    feedback_ref = _require_request_ref(
        value.get("feedback_ref"),
        "FEEDBACK_RECORD_REF_INVALID",
    )
    _require_iso_utc(value.get("received_at"), "FEEDBACK_RECORD_TIME_INVALID")
    feedback = _require_mapping(value.get("feedback"), "FEEDBACK_RECORD_REQUEST_INVALID")
    validate_feedback_request(feedback)
    if feedback_ref_for_request(feedback) != feedback_ref:
        raise ValueError("FEEDBACK_RECORD_REF_MISMATCH")
    return value


def feedback_record_to_episode(value: dict[str, Any]) -> dict[str, Any]:
    record = validate_feedback_record(value)
    feedback = record["feedback"]
    result_ref = feedback.get("result_request_ref")
    episode = {
        "schema": EXPERIENCE_SCHEMA,
        "episode_id": record["feedback_ref"],
        "observed_at": record["received_at"],
        "failure_class": feedback["failure_class"],
        "operator": {
            "name": feedback["operator"]["name"],
            "version": feedback["operator"]["version"],
        },
        "outcome": feedback["outcome"],
        "confidence": "FACT",
        "aggregation_eligible": True,
        "baseline": {
            "status": "EXPLICIT_FEEDBACK",
            "request_ref": feedback["baseline_request_ref"],
        },
        "result": {
            "status": "EXPLICIT_FEEDBACK" if result_ref is not None else "NOT_PROVIDED",
            "request_ref": result_ref,
        },
        "provenance": [
            {"kind": "feedback", "ref": record["feedback_ref"]},
        ],
    }
    return validate_episode(episode)


def validate_episode(value: dict[str, Any]) -> dict[str, Any]:
    value = _require_mapping(value, "EXPERIENCE_INVALID")
    _forbid_private_text(value, "EXPERIENCE_PRIVATE_TEXT_FORBIDDEN")
    _require_no_unknown_fields(
        value,
        _EXPERIENCE_FIELDS,
        "EXPERIENCE_FIELDS_INVALID",
    )
    if value.get("schema") != EXPERIENCE_SCHEMA:
        raise ValueError("EXPERIENCE_SCHEMA_INVALID")

    _require_string(value.get("episode_id"), "EXPERIENCE_ID_INVALID")
    _require_string(value.get("observed_at"), "EXPERIENCE_OBSERVED_AT_INVALID")
    _require_slug(value.get("failure_class"), "EXPERIENCE_FAILURE_CLASS_INVALID")
    _validate_operator(value.get("operator"))

    outcome = value.get("outcome")
    if outcome not in _OUTCOMES:
        raise ValueError("EXPERIENCE_OUTCOME_INVALID")

    confidence = value.get("confidence")
    if confidence not in _CONFIDENCE:
        raise ValueError("EXPERIENCE_CONFIDENCE_INVALID")

    if not isinstance(value.get("aggregation_eligible"), bool):
        raise ValueError("EXPERIENCE_AGGREGATION_ELIGIBLE_INVALID")

    baseline = _require_mapping(value.get("baseline"), "EXPERIENCE_BASELINE_INVALID")
    _require_string(baseline.get("status"), "EXPERIENCE_BASELINE_STATUS_INVALID")
    _require_string(baseline.get("request_ref"), "EXPERIENCE_BASELINE_REF_INVALID")

    result = _require_mapping(value.get("result"), "EXPERIENCE_RESULT_INVALID")
    _require_string(result.get("status"), "EXPERIENCE_RESULT_STATUS_INVALID")
    result_ref = result.get("request_ref")
    if result_ref is not None:
        _require_string(result_ref, "EXPERIENCE_RESULT_REF_INVALID")

    provenance = value.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        raise ValueError("EXPERIENCE_PROVENANCE_INVALID")
    for item in provenance:
        item = _require_mapping(item, "EXPERIENCE_PROVENANCE_INVALID")
        _require_no_unknown_fields(
            item,
            _PROVENANCE_FIELDS,
            "EXPERIENCE_PROVENANCE_FIELDS_INVALID",
        )
        _require_slug(item.get("kind"), "EXPERIENCE_PROVENANCE_INVALID")
        _require_string(item.get("ref"), "EXPERIENCE_PROVENANCE_INVALID")

    return value


def load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("EXPERIENCE_UTF8_INVALID") from exc
    except OSError as exc:
        raise ValueError(f"EXPERIENCE_READ_FAILED:{exc.__class__.__name__}") from exc

    rows: list[dict[str, Any]] = []
    seen_episode_ids: set[str] = set()
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            raise ValueError(f"EXPERIENCE_JSONL_EMPTY_LINE:{line_number}")
        try:
            row = loads_strict(raw)
        except StrictJsonError as exc:
            raise ValueError(f"EXPERIENCE_JSON_INVALID:{line_number}:{exc}") from exc
        row = validate_episode(row)
        episode_id = row["episode_id"]
        if episode_id in seen_episode_ids:
            raise ValueError(f"EXPERIENCE_DUPLICATE_ID:{episode_id}")
        seen_episode_ids.add(episode_id)
        rows.append(row)
    return rows


def aggregate_patterns(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "observed": 0,
            "eligible": 0,
            "ineligible": 0,
            "outcomes": {name: 0 for name in _OUTCOMES},
        }
    )

    for episode in episodes:
        validate_episode(episode)
        operator = episode["operator"]
        key = (
            f'{episode["failure_class"]}::'
            f'{operator["name"]}::'
            f'{operator["version"]}'
        )
        group = groups[key]
        group["observed"] += 1
        if episode["aggregation_eligible"]:
            group["eligible"] += 1
            group["outcomes"][episode["outcome"]] += 1
        else:
            group["ineligible"] += 1

    return {
        "schema": PATTERN_SCHEMA,
        "episode_count": len(episodes),
        "patterns": dict(sorted(groups.items())),
        "policy_candidates": [],
        "policy_promotion": "NOT_PERFORMED",
    }
