#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

MARKER_PREFIX = "<!-- alternative-review-state:v1 "
MARKER_SUFFIX = " -->"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TRUSTED_ASSOCIATIONS = {"OWNER"}


def normalize_comments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("Alternative review evidence must be a JSON list or list of pages")
    if all(isinstance(item, dict) for item in value):
        return value

    flattened: list[dict[str, Any]] = []
    for page in value:
        if not isinstance(page, list):
            raise ValueError(
                "Alternative review paginated evidence must contain JSON arrays"
            )
        for item in page:
            if not isinstance(item, dict):
                raise ValueError("Alternative review comment entries must be JSON objects")
            flattened.append(item)
    return flattened


def _extract_receipt(body: str) -> tuple[bool, dict[str, Any] | None]:
    for line in str(body).splitlines():
        line = line.strip()
        if not line.startswith(MARKER_PREFIX):
            continue
        if not line.endswith(MARKER_SUFFIX):
            return True, None
        raw = line[len(MARKER_PREFIX):-len(MARKER_SUFFIX)]
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return True, None
        return True, value if isinstance(value, dict) else None
    return False, None


def _invalid(head_sha: str, reason: str) -> dict[str, Any]:
    return {
        "status": "INVALID",
        "reason": reason,
        "head_sha": head_sha,
        "reviewed_commit": None,
        "reviewer_count": 0,
        "fallback": True,
    }


def _validate_receipt(receipt: dict[str, Any], expected_head: str) -> dict[str, Any]:
    head = str(receipt.get("head_sha") or "")
    if not HEX40.fullmatch(head):
        return _invalid(expected_head, "head-sha-invalid")
    if head != expected_head:
        return {
            "status": "STALE",
            "reason": "review-not-for-head",
            "head_sha": expected_head,
            "reviewed_commit": head,
            "reviewer_count": 0,
            "fallback": True,
        }

    status = str(receipt.get("status") or "")
    if status not in {"CLEAN", "FINDINGS"}:
        return _invalid(expected_head, "receipt-status-invalid")
    if not str(receipt.get("reviewed_at") or "").strip():
        return _invalid(expected_head, "reviewed-at-missing")
    if not str(receipt.get("operator_version") or "").strip():
        return _invalid(expected_head, "operator-version-missing")

    reviewers = receipt.get("reviewers")
    if not isinstance(reviewers, list) or not reviewers:
        return _invalid(expected_head, "reviewers-invalid")

    identities: set[tuple[str, str]] = set()
    any_findings = False
    for reviewer in reviewers:
        if not isinstance(reviewer, dict):
            return _invalid(expected_head, "reviewer-invalid")
        route = str(reviewer.get("provider_route") or "").strip()
        model = str(reviewer.get("model") or "").strip()
        verdict = str(reviewer.get("verdict") or "")
        result_sha = str(reviewer.get("result_sha256") or "")
        if not route or not model:
            return _invalid(expected_head, "reviewer-identity-invalid")
        if verdict not in {"CLEAN", "FINDINGS"}:
            return _invalid(expected_head, "reviewer-verdict-invalid")
        if not HEX64.fullmatch(result_sha):
            return _invalid(expected_head, "review-result-sha256-invalid")
        identities.add((route, model))
        any_findings = any_findings or verdict == "FINDINGS"

    if any_findings:
        return {
            "status": "FINDINGS",
            "reason": "reviewer-findings",
            "head_sha": expected_head,
            "reviewed_commit": head,
            "reviewer_count": len(identities),
            "fallback": True,
        }
    if status != "CLEAN":
        return _invalid(expected_head, "receipt-status-reviewer-mismatch")
    if len(identities) < 2:
        return _invalid(expected_head, "insufficient-distinct-reviewers")

    return {
        "status": "CLEAN",
        "reason": "alternative-reviews-clean",
        "head_sha": expected_head,
        "reviewed_commit": head,
        "reviewer_count": len(identities),
        "fallback": True,
    }


def classify_alternative_review(
    comments: list[dict[str, Any]], head_sha: str
) -> dict[str, Any]:
    if not HEX40.fullmatch(str(head_sha)):
        raise ValueError("head_sha must be a lowercase 40-hex Git SHA")

    markers: list[tuple[str, dict[str, Any] | None]] = []
    for comment in comments:
        association = str(comment.get("author_association") or "")
        if association not in TRUSTED_ASSOCIATIONS:
            continue
        found, receipt = _extract_receipt(str(comment.get("body") or ""))
        if not found:
            continue
        updated = str(comment.get("updated_at") or comment.get("created_at") or "")
        markers.append((updated, receipt))

    base = {
        "head_sha": head_sha,
        "reviewed_commit": None,
        "reviewer_count": 0,
        "fallback": True,
    }
    if not markers:
        return {
            **base,
            "status": "PENDING",
            "reason": "trusted-review-receipt-missing",
        }

    _updated, latest = max(markers, key=lambda item: item[0])
    if latest is None:
        return {**base, "status": "INVALID", "reason": "malformed-review-receipt"}
    return _validate_receipt(latest, head_sha)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--input", type=pathlib.Path)
    args = parser.parse_args()

    raw = (
        json.loads(args.input.read_text(encoding="utf-8"))
        if args.input
        else json.load(sys.stdin)
    )
    try:
        comments = normalize_comments(raw)
        result = classify_alternative_review(comments, args.head)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
