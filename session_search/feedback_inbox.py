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
from session_search.strict_json import StrictJsonError, loads_strict


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


def _publish_record(
    store_dir: pathlib.Path,
    record: dict[str, Any],
    *,
    exact_duplicate: bool,
) -> bool:
    """Publish an immutable record; return True only when this call created it."""
    record = validate_feedback_record(record)
    _ensure_private_dir(store_dir)
    feedback_ref = record["feedback_ref"]
    destination = store_dir / f"{feedback_ref}.json"
    payload = (
        json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".pending-", dir=store_dir)
    created = False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp_name, destination)  # Atomic no-overwrite publication.
            created = True
        except FileExistsError:
            prior = _read_record(destination)
            if prior["feedback"] != record["feedback"] or (exact_duplicate and prior != record):
                raise ValueError("FEEDBACK_EXCHANGE_CONFLICT")
        dir_fd = os.open(store_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        pathlib.Path(tmp_name).unlink(missing_ok=True)
    persisted = _read_record(destination)
    if (exact_duplicate and persisted != record) or persisted["feedback"] != record["feedback"]:
        raise ValueError("FEEDBACK_INBOX_READBACK_FAILED")
    return created


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
    record = {
        "schema": FEEDBACK_RECORD_SCHEMA,
        "feedback_ref": feedback_ref,
        "received_at": received_at,
        "feedback": feedback,
    }
    _publish_record(store_dir, record, exact_duplicate=False)
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


def _read_exchange(path: pathlib.Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("FEEDBACK_EXCHANGE_INVALID_SOURCE")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("FEEDBACK_EXCHANGE_NOT_PRIVATE")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("FEEDBACK_EXCHANGE_UTF8_INVALID") from exc
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines, 1):
        if not line.strip():
            raise ValueError(f"FEEDBACK_EXCHANGE_EMPTY_LINE:{index}")
        try:
            record = validate_feedback_record(loads_strict(line))
        except StrictJsonError as exc:
            raise ValueError(f"FEEDBACK_EXCHANGE_INVALID_JSON:{index}") from exc
        ref = record["feedback_ref"]
        if ref in seen:
            raise ValueError(f"FEEDBACK_EXCHANGE_DUPLICATE_REF:{index}")
        seen.add(ref)
        records.append(record)
    return records


def export_feedback_records(store_dir: pathlib.Path, output_path: pathlib.Path) -> int:
    """Write one private, stable JSONL snapshot without overwriting an earlier snapshot."""
    records = read_feedback_records(store_dir)
    _ensure_private_dir(output_path.parent)
    payload = (
        "\n".join(
            json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            for record in records
        )
        + ("\n" if records else "")
    ).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=".feedback-export-", dir=output_path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp_name, output_path)  # Fail if snapshot already exists.
        dir_fd = os.open(output_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        pathlib.Path(tmp_name).unlink(missing_ok=True)
    if len(_read_exchange(output_path)) != len(records):
        raise ValueError("FEEDBACK_EXCHANGE_READBACK_FAILED")
    return len(records)


def import_feedback_records(source: pathlib.Path, store_dir: pathlib.Path) -> tuple[int, int]:
    """Preflight whole input + conflicts, then idempotently append exact records."""
    records = _read_exchange(source)
    prior = {record["feedback_ref"]: record for record in read_feedback_records(store_dir)}
    for record in records:
        existing = prior.get(record["feedback_ref"])
        if existing is not None and existing != record:
            raise ValueError("FEEDBACK_EXCHANGE_CONFLICT")
    added = 0
    for record in records:
        added += int(_publish_record(store_dir, record, exact_duplicate=True))
    return added, len(records) - added


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
    export = actions.add_parser("export")
    export.add_argument("--store-dir", type=pathlib.Path, required=True)
    export.add_argument("--output", type=pathlib.Path, required=True)
    ingest = actions.add_parser("import")
    ingest.add_argument("--source", type=pathlib.Path, required=True)
    ingest.add_argument("--store-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.action == "submit":
        request = loads_strict(sys.stdin.read())
        ref = submit_feedback(args.store_dir, request)
        print(json.dumps({"feedback_ref": ref}))
    elif args.action == "export":
        count = export_feedback_records(args.store_dir, args.output)
        print(json.dumps({"records": count}))
    elif args.action == "import":
        added, unchanged = import_feedback_records(args.source, args.store_dir)
        print(json.dumps({"imported": added, "unchanged": unchanged}, sort_keys=True))
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
