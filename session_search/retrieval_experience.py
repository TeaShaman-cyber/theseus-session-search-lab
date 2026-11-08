from __future__ import annotations

import json
import pathlib
import re
from collections import defaultdict
from typing import Any

FEEDBACK_REQUEST_SCHEMA = "theseus.session-search-feedback-request.v0"
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
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")


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


def _forbid_private_text(mapping: dict[str, Any], code: str) -> None:
    if _PRIVATE_TEXT_KEYS.intersection(mapping):
        raise ValueError(code)


def _validate_operator(value: Any) -> dict[str, Any]:
    operator = _require_mapping(value, "EXPERIENCE_OPERATOR_INVALID")
    _require_slug(operator.get("name"), "EXPERIENCE_OPERATOR_INVALID")
    _require_slug(operator.get("version"), "EXPERIENCE_OPERATOR_INVALID")
    return operator


def validate_feedback_request(value: dict[str, Any]) -> dict[str, Any]:
    value = _require_mapping(value, "FEEDBACK_REQUEST_INVALID")
    _forbid_private_text(value, "FEEDBACK_PRIVATE_TEXT_FORBIDDEN")
    if value.get("schema") != FEEDBACK_REQUEST_SCHEMA:
        raise ValueError("FEEDBACK_SCHEMA_INVALID")

    _require_string(value.get("baseline_request_ref"), "FEEDBACK_BASELINE_REF_INVALID")
    result_ref = value.get("result_request_ref")
    if result_ref is not None:
        _require_string(result_ref, "FEEDBACK_RESULT_REF_INVALID")
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


def validate_episode(value: dict[str, Any]) -> dict[str, Any]:
    value = _require_mapping(value, "EXPERIENCE_INVALID")
    _forbid_private_text(value, "EXPERIENCE_PRIVATE_TEXT_FORBIDDEN")
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
        _require_slug(item.get("kind"), "EXPERIENCE_PROVENANCE_INVALID")
        _require_string(item.get("ref"), "EXPERIENCE_PROVENANCE_INVALID")

    return value


def load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"EXPERIENCE_JSON_INVALID:{line_number}") from exc
        rows.append(validate_episode(row))
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
