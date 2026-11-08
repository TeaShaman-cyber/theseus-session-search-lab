from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from session_search.retrieval_experience import validate_feedback_record
from session_search.strict_json import StrictJsonError, loads_strict

DEFAULT_FEEDBACK_HASH_KEY = "theseus:session-search:feedback:v0"


def _normalize_https_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("FEEDBACK_SOURCE_URL_INVALID")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("FEEDBACK_SOURCE_URL_INVALID")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path or "/", "", "")
    )


def _default_post_json(
    rest_url: str,
    token: str,
    command: list[Any],
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    payload = json.dumps(
        command,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        rest_url,
        data=payload,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, UnicodeDecodeError) as exc:
        raise ValueError("FEEDBACK_SOURCE_FAILED") from exc

    try:
        parsed = loads_strict(raw)
    except StrictJsonError as exc:
        raise ValueError("FEEDBACK_SOURCE_RESPONSE_INVALID") from exc
    if not isinstance(parsed, dict):
        raise ValueError("FEEDBACK_SOURCE_RESPONSE_INVALID")
    return parsed


def scan_upstash_feedback_records(
    *,
    rest_url: str,
    token: str,
    hash_key: str = DEFAULT_FEEDBACK_HASH_KEY,
    count: int = 500,
    max_pages: int = 10_000,
    post_json: Callable[[list[Any]], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    normalized_url = _normalize_https_url(rest_url)
    if not isinstance(token, str) or not token:
        raise ValueError("FEEDBACK_SOURCE_TOKEN_INVALID")
    if not isinstance(hash_key, str) or not hash_key or len(hash_key) > 128:
        raise ValueError("FEEDBACK_SOURCE_KEY_INVALID")
    if not isinstance(count, int) or count < 1 or count > 10_000:
        raise ValueError("FEEDBACK_SOURCE_COUNT_INVALID")
    if not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("FEEDBACK_SOURCE_MAX_PAGES_INVALID")

    if post_json is None:
        def post_json(command: list[Any]) -> dict[str, Any]:
            return _default_post_json(normalized_url, token, command)

    cursor = "0"
    seen_cursors: set[str] = set()
    seen_refs: set[str] = set()
    records: list[dict[str, Any]] = []

    for _ in range(max_pages):
        if cursor in seen_cursors:
            raise ValueError("FEEDBACK_SOURCE_CURSOR_LOOP")
        seen_cursors.add(cursor)

        payload = post_json([
            "HSCAN",
            hash_key,
            cursor,
            "COUNT",
            count,
        ])
        if not isinstance(payload, dict):
            raise ValueError("FEEDBACK_SOURCE_RESPONSE_INVALID")
        if payload.get("error") is not None:
            raise ValueError("FEEDBACK_SOURCE_FAILED")
        result = payload.get("result")
        if (
            not isinstance(result, list)
            or len(result) != 2
            or not isinstance(result[1], list)
            or len(result[1]) % 2 != 0
        ):
            raise ValueError("FEEDBACK_SOURCE_RESPONSE_INVALID")

        next_cursor_raw, flat = result
        if isinstance(next_cursor_raw, int):
            next_cursor = str(next_cursor_raw)
        elif isinstance(next_cursor_raw, str) and next_cursor_raw.isdigit():
            next_cursor = next_cursor_raw
        else:
            raise ValueError("FEEDBACK_SOURCE_RESPONSE_INVALID")

        for index in range(0, len(flat), 2):
            field = flat[index]
            raw_record = flat[index + 1]
            if not isinstance(field, str) or not isinstance(raw_record, str):
                raise ValueError("FEEDBACK_SOURCE_RESPONSE_INVALID")
            if field in seen_refs:
                raise ValueError("FEEDBACK_SOURCE_DUPLICATE_REF")
            try:
                record = loads_strict(raw_record)
            except StrictJsonError as exc:
                raise ValueError("FEEDBACK_SOURCE_RECORD_INVALID") from exc
            if not isinstance(record, dict):
                raise ValueError("FEEDBACK_SOURCE_RECORD_INVALID")
            validate_feedback_record(record)
            if record["feedback_ref"] != field:
                raise ValueError("FEEDBACK_SOURCE_FIELD_MISMATCH")
            seen_refs.add(field)
            records.append(record)

        if next_cursor == "0":
            return sorted(records, key=lambda row: row["feedback_ref"])
        cursor = next_cursor

    raise ValueError("FEEDBACK_SOURCE_MAX_PAGES_EXCEEDED")
