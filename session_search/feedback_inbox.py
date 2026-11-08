"""Opt-in private, local agent feedback inbox; not a production serving store."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import stat
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

from session_search.feedback_refresh import build_feedback_refresh, write_feedback_refresh
from session_search.retrieval_experience import (
    FEEDBACK_RECORD_SCHEMA,
    feedback_ref_for_request,
    load_jsonl,
    validate_feedback_record,
    validate_feedback_request,
)
from session_search.strict_json import loads_strict


def _ensure_private_dir(path: pathlib.Path) -> None:
    if path.is_symlink():
        raise ValueError("FEEDBACK_INBOX_SYMLINK_FORBIDDEN")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("FEEDBACK_INBOX_NOT_PRIVATE")


def _read_record(path: pathlib.Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("FEEDBACK_INBOX_INVALID_ENTRY")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("FEEDBACK_INBOX_RECORD_NOT_PRIVATE")
    record = validate_feedback_record(loads_strict(path.read_text(encoding="utf-8")))
    if path.stem != record["feedback_ref"]:
        raise ValueError("FEEDBACK_INBOX_FILENAME_MISMATCH")
    return record


def submit_feedback(
    store_dir: pathlib.Path,
    feedback: dict[str, Any],
    *,
    received_at: str | None = None,
) -> str:
    validate_feedback_request(feedback)
    feedback_ref = feedback_ref_for_request(feedback)
    if received_at is None:
        received_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    record = validate_feedback_record({
        "schema": FEEDBACK_RECORD_SCHEMA,
        "feedback_ref": feedback_ref,
        "received_at": received_at,
        "feedback": feedback,
    })
    _ensure_private_dir(store_dir)
    destination = store_dir / f"{feedback_ref}.json"
    payload = (json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".pending-", dir=store_dir)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp_name, destination)  # Atomic publish; never overwrite a prior episode.
        except FileExistsError:
            prior = _read_record(destination)
            if feedback_ref_for_request(prior["feedback"]) != feedback_ref:
                raise ValueError("FEEDBACK_INBOX_ID_CONFLICT")
        dir_fd = os.open(store_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        pathlib.Path(tmp_name).unlink(missing_ok=True)
    if _read_record(destination)["feedback_ref"] != feedback_ref:
        raise ValueError("FEEDBACK_INBOX_READBACK_FAILED")
    return feedback_ref


def read_feedback_records(store_dir: pathlib.Path) -> list[dict[str, Any]]:
    if not store_dir.exists():
        return []
    _ensure_private_dir(store_dir)
    records = []
    for path in sorted(store_dir.iterdir()):
        if path.name.startswith(".pending-"):
            continue
        if path.suffix != ".json":
            raise ValueError("FEEDBACK_INBOX_UNEXPECTED_ENTRY")
        records.append(_read_record(path))
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Private local agent feedback inbox")
    actions = parser.add_subparsers(dest="action", required=True)
    submit = actions.add_parser("submit")
    submit.add_argument("--store-dir", type=pathlib.Path, required=True)
    refresh = actions.add_parser("refresh")
    refresh.add_argument("--store-dir", type=pathlib.Path, required=True)
    refresh.add_argument("--episodes", type=pathlib.Path, required=True)
    refresh.add_argument("--patterns", type=pathlib.Path, required=True)
    refresh.add_argument("--seed", type=pathlib.Path)
    args = parser.parse_args()
    if args.action == "submit":
        request = loads_strict(sys.stdin.read())
        ref = submit_feedback(args.store_dir, request)
        print(json.dumps({"feedback_ref": ref}))
    else:
        seeds = load_jsonl(args.seed) if args.seed else []
        payload = build_feedback_refresh(read_feedback_records(args.store_dir), seed_episodes=seeds)
        write_feedback_refresh(payload, episodes_path=args.episodes, patterns_path=args.patterns)
        print(json.dumps({
            "episodes": len(payload["episodes"]),
            "patterns": len(payload["patterns"]["patterns"]),
            "policy_promotion": payload["patterns"]["policy_promotion"],
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
