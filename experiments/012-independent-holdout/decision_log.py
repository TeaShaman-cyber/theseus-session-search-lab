#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3

from session_search.corpus_lock import resolve_corpus_lock


def content_fp(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _candidate(discovery: list[dict], index: int) -> dict:
    if not isinstance(index, int) or index < 0 or index >= len(discovery):
        raise ValueError("CANDIDATE_INDEX_INVALID")
    return discovery[index]


def make_accept_decision(
    db_path: pathlib.Path,
    discovery: list[dict],
    *,
    index: int,
    target_session_id: str,
    target_message_id: str,
    reason: str,
) -> dict:
    query = _candidate(discovery, index)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT session_id,message_id,create_time,text
            FROM messages
            WHERE session_id=? AND message_id=?
            """,
            [target_session_id, target_message_id],
        ).fetchall()
    finally:
        conn.close()
    if len(rows) != 1:
        raise ValueError("TARGET_LOCATOR_NOT_UNIQUE")
    target = dict(rows[0])
    if target["create_time"] is None or query.get("create_time") is None:
        raise ValueError("TARGET_TIME_MISSING")
    if float(target["create_time"]) >= float(query["create_time"]):
        raise ValueError("TARGET_NOT_BEFORE_QUERY")
    if not isinstance(target.get("text"), str) or not target["text"].strip():
        raise ValueError("TARGET_TEXT_INVALID")
    return {
        "index": index,
        "status": "accept",
        "reason": reason,
        "target_session_id": target_session_id,
        "target_message_id": target_message_id,
        "target_content_fp": content_fp(target["text"]),
    }


def make_reject_decision(*, index: int, reason: str) -> dict:
    if not isinstance(index, int) or index < 0:
        raise ValueError("CANDIDATE_INDEX_INVALID")
    if not reason.strip():
        raise ValueError("REASON_REQUIRED")
    return {"index": index, "status": "reject", "reason": reason}


def upsert_decision(current: list[dict], decision: dict) -> list[dict]:
    by_index = {row["index"]: row for row in current}
    existing = by_index.get(decision["index"])
    if existing is not None and existing != decision:
        raise ValueError("DECISION_CONFLICT")
    by_index[decision["index"]] = decision
    return [by_index[index] for index in sorted(by_index)]


def freeze_holdout(discovery: list[dict], decisions: list[dict], *, count: int = 10) -> list[dict]:
    if count < 1:
        raise ValueError("COUNT_INVALID")
    accepted = [row for row in sorted(decisions, key=lambda row: row["index"]) if row["status"] == "accept"]
    if len(accepted) < count:
        raise ValueError("INSUFFICIENT_ACCEPTED_CASES")
    frozen = []
    for ordinal, decision in enumerate(accepted[:count], 1):
        source = _candidate(discovery, decision["index"])
        frozen.append(
            {
                "case_id": f"h203-{ordinal:03d}",
                "source_index": decision["index"],
                "query_text": source["text"],
                "target_content_fp": decision["target_content_fp"],
            }
        )
    return frozen


def _read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_private(path: pathlib.Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--discovery", type=pathlib.Path, required=True)
    common.add_argument("--log", type=pathlib.Path, required=True)
    common.add_argument("--index", type=int, required=True)
    common.add_argument("--reason", required=True)

    reject = sub.add_parser("reject", parents=[common])

    accept = sub.add_parser("accept", parents=[common])
    accept.add_argument("--corpus-lock", type=pathlib.Path, required=True)
    accept.add_argument("--target-session-id", required=True)
    accept.add_argument("--target-message-id", required=True)

    freeze = sub.add_parser("freeze")
    freeze.add_argument("--discovery", type=pathlib.Path, required=True)
    freeze.add_argument("--log", type=pathlib.Path, required=True)
    freeze.add_argument("--out", type=pathlib.Path, required=True)
    freeze.add_argument("--count", type=int, default=10)

    args = parser.parse_args()
    discovery = _read_json(args.discovery)
    decisions = _read_json(args.log) if args.log.exists() else []

    if args.command == "reject":
        decision = make_reject_decision(index=args.index, reason=args.reason)
        _write_private(args.log, upsert_decision(decisions, decision))
        print(json.dumps({"index": args.index, "status": "reject"}, sort_keys=True))
        return 0

    if args.command == "accept":
        db = resolve_corpus_lock(args.corpus_lock)
        decision = make_accept_decision(
            db,
            discovery,
            index=args.index,
            target_session_id=args.target_session_id,
            target_message_id=args.target_message_id,
            reason=args.reason,
        )
        _write_private(args.log, upsert_decision(decisions, decision))
        print(json.dumps({"index": args.index, "status": "accept", "target_content_fp": decision["target_content_fp"]}, sort_keys=True))
        return 0

    frozen = freeze_holdout(discovery, decisions, count=args.count)
    _write_private(args.out, frozen)
    print(json.dumps({"frozen_cases": len(frozen)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
