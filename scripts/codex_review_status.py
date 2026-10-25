#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

MARKER_PREFIX = "<!-- codex-review-state:v1 "
MARKER_SUFFIX = " -->"
CODEX_LOGIN_PREFIX = "chatgpt-codex-connector"


def _extract_state(body: str) -> dict[str, Any] | None:
    for line in str(body).splitlines():
        line = line.strip()
        if not line.startswith(MARKER_PREFIX) or not line.endswith(MARKER_SUFFIX):
            continue
        raw = line[len(MARKER_PREFIX):-len(MARKER_SUFFIX)]
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    return None


def normalize_comments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("Codex review evidence must be a JSON list or list of pages")

    if all(isinstance(item, dict) for item in value):
        return value

    flattened: list[dict[str, Any]] = []
    for page in value:
        if not isinstance(page, list):
            raise ValueError("Codex paginated review evidence must contain JSON arrays")
        for item in page:
            if not isinstance(item, dict):
                raise ValueError("Codex review comment entries must be JSON objects")
            flattened.append(item)
    return flattened


def classify_codex_review(comments: list[dict[str, Any]], head_sha: str) -> dict[str, Any]:
    observations = []
    for comment in comments:
        login = str((comment.get("user") or {}).get("login") or "")
        if not login.startswith(CODEX_LOGIN_PREFIX):
            continue
        state = _extract_state(str(comment.get("body") or ""))
        if state is None:
            continue
        code_review = state.get("code_review")
        if not isinstance(code_review, dict):
            continue
        observations.append({
            "updated_at": str(comment.get("updated_at") or comment.get("created_at") or ""),
            "review": code_review,
        })

    base = {"advisory": True, "head_sha": head_sha}
    if not observations:
        return {**base, "status": "PENDING", "reason": "review-summary-missing", "reviewed_commit": None}

    matching = [item for item in observations if item["review"].get("commit_sha") == head_sha]
    if not matching:
        latest = max(observations, key=lambda item: item["updated_at"])
        return {
            **base,
            "status": "STALE",
            "reason": "review-not-for-head",
            "reviewed_commit": latest["review"].get("commit_sha"),
        }

    current = max(matching, key=lambda item: item["updated_at"])
    review = current["review"]
    status = str(review.get("status") or "").lower()
    unresolved = review.get("has_unresolved_findings")

    if status != "completed":
        return {
            **base,
            "status": "PENDING",
            "reason": "review-running" if status in {"running", "queued", "in_progress"} else "review-nonterminal",
            "reviewed_commit": review.get("commit_sha"),
        }

    if unresolved is True:
        return {
            **base,
            "status": "FINDINGS",
            "reason": "unresolved-findings",
            "reviewed_commit": review.get("commit_sha"),
        }

    if unresolved is False:
        return {
            **base,
            "status": "CLEAN",
            "reason": "completed-clean",
            "reviewed_commit": review.get("commit_sha"),
        }

    return {
        **base,
        "status": "PENDING",
        "reason": "review-resolution-unknown",
        "reviewed_commit": review.get("commit_sha"),
    }


def classify_codex_wait(
    review_state: dict[str, Any], *, elapsed_seconds: int, timeout_seconds: int
) -> dict[str, Any]:
    if elapsed_seconds < 0:
        raise ValueError("elapsed_seconds must be non-negative")
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    status = str(review_state.get("status") or "")
    if status in {"CLEAN", "FINDINGS"}:
        disposition = "TERMINAL"
    elif elapsed_seconds >= timeout_seconds:
        disposition = "TIMEOUT"
    else:
        disposition = "WAIT"
    return {
        **review_state,
        "wait_disposition": disposition,
        "wait_elapsed_seconds": elapsed_seconds,
        "wait_timeout_seconds": timeout_seconds,
    }


def bounded_poll_sleep_seconds(
    *, elapsed_seconds: int, timeout_seconds: int, poll_seconds: int
) -> int:
    if elapsed_seconds < 0:
        raise ValueError("elapsed_seconds must be non-negative")
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    remaining = max(0, timeout_seconds - elapsed_seconds)
    return min(poll_seconds, remaining)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--input", type=pathlib.Path)
    args = parser.parse_args()

    raw_comments = json.loads(args.input.read_text(encoding="utf-8")) if args.input else json.load(sys.stdin)
    try:
        comments = normalize_comments(raw_comments)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(classify_codex_review(comments, args.head), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
