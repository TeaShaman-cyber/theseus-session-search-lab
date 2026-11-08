"""Optional PostgreSQL replica for immutable, backend-neutral feedback records.

No credentials, remote endpoints or deployment choices are embedded here.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import pathlib
import re
import subprocess
from collections.abc import Callable, Iterable
from typing import Any

from session_search.feedback_inbox import _publish_record, read_feedback_records
from session_search.retrieval_experience import validate_feedback_record
from session_search.strict_json import loads_strict

MAX_RECORDS = 1000
MAX_OUTPUT_BYTES = 12 * 1024 * 1024
TABLE = "session_feedback.records_v0"


def encode_record(record: dict[str, Any]) -> str:
    """Encode validated private record as one ASCII-safe SQL COPY line."""
    validate_feedback_record(record)
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return base64.b64encode(canonical.encode("utf-8")).decode("ascii")


def decode_remote_records(lines: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_lines = lines.splitlines()
    if len(raw_lines) > MAX_RECORDS:
        raise ValueError("FEEDBACK_REPLICA_ROWS_EXCEEDED")
    for line in raw_lines:
        try:
            decoded = base64.b64decode(line, validate=True).decode("utf-8")
        except (UnicodeDecodeError, binascii.Error, ValueError) as exc:
            raise ValueError("FEEDBACK_REPLICA_INVALID_BASE64") from exc
        record = validate_feedback_record(loads_strict(decoded))
        ref = record["feedback_ref"]
        if ref in seen:
            raise ValueError("FEEDBACK_REPLICA_DUPLICATE")
        seen.add(ref)
        result.append(record)
    return sorted(result, key=lambda x: x["feedback_ref"])


def plan_reconciliation(
    local: Iterable[dict[str, Any]], remote: Iterable[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def index(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        data = {}
        for row in rows:
            validate_feedback_record(row)
            ref = row["feedback_ref"]
            if ref in data:
                raise ValueError("FEEDBACK_REPLICA_DUPLICATE")
            data[ref] = row
        return data

    a, b = index(local), index(remote)
    for key in a.keys() & b.keys():
        if a[key] != b[key]:
            raise ValueError("FEEDBACK_REPLICA_CONFLICT")
    return ([a[k] for k in sorted(a.keys() - b.keys())],
            [b[k] for k in sorted(b.keys() - a.keys())])


def sql_for_import(records: list[dict[str, Any]]) -> str:
    if not records or len(records) > MAX_RECORDS:
        raise ValueError("FEEDBACK_REPLICA_BATCH_INVALID")
    refs = set()
    for row in records:
        validate_feedback_record(row)
        if row["feedback_ref"] in refs:
            raise ValueError("FEEDBACK_REPLICA_DUPLICATE")
        refs.add(row["feedback_ref"])
    payload = "\n".join(encode_record(row) for row in records)
    return f"""BEGIN;
CREATE TEMP TABLE feedback_replica_stage (encoded text NOT NULL) ON COMMIT DROP;
COPY feedback_replica_stage (encoded) FROM STDIN;
{payload}
\\.
CREATE TEMP VIEW feedback_replica_decoded AS
  SELECT convert_from(decode(encoded, 'base64'), 'UTF8')::jsonb AS payload
  FROM feedback_replica_stage;
DO $check$
BEGIN
  IF EXISTS (
    SELECT 1 FROM feedback_replica_decoded s
    WHERE s.payload->>'schema' <> 'theseus.session-search-feedback-record.v0'
       OR s.payload->>'feedback_ref' IS NULL
  ) THEN
    RAISE EXCEPTION 'FEEDBACK_REPLICA_SCHEMA_INVALID';
  END IF;
  IF EXISTS (
    SELECT 1 FROM feedback_replica_decoded s
    JOIN {TABLE} d ON d.feedback_ref = s.payload->>'feedback_ref'
    WHERE d.record <> s.payload
  ) THEN
    RAISE EXCEPTION 'FEEDBACK_REPLICA_CONFLICT';
  END IF;
END
$check$;
INSERT INTO {TABLE} (feedback_ref, record)
SELECT payload->>'feedback_ref', payload FROM feedback_replica_decoded
ON CONFLICT (feedback_ref) DO NOTHING;
DO $verify$
BEGIN
  IF EXISTS (
    SELECT 1 FROM feedback_replica_decoded s
    LEFT JOIN {TABLE} d ON d.feedback_ref = s.payload->>'feedback_ref'
    WHERE d.record IS DISTINCT FROM s.payload
  ) THEN
    RAISE EXCEPTION 'FEEDBACK_REPLICA_CONFLICT';
  END IF;
END
$verify$;
COMMIT;
"""


def reconcile_feedback(
    store_dir: pathlib.Path,
    read_remote: Callable[[], list[dict[str, Any]]],
    append_remote: Callable[[list[dict[str, Any]]], None],
) -> dict[str, int]:
    local = read_feedback_records(store_dir)
    pending_up, _ = plan_reconciliation(local, read_remote())
    if pending_up:
        append_remote(pending_up)
    remote = read_remote()  # Independent remote readback, not writer self-report.
    missing_remote, pending_down = plan_reconciliation(local, remote)
    if missing_remote:
        raise RuntimeError("FEEDBACK_REPLICA_REMOTE_READBACK_INCOMPLETE")
    for record in pending_down:
        _publish_record(store_dir, record, exact_duplicate=True)
    updated = read_feedback_records(store_dir)
    if plan_reconciliation(updated, read_remote()) != ([], []):
        raise RuntimeError("FEEDBACK_REPLICA_FINAL_MISMATCH")
    return {"uploaded": len(pending_up), "downloaded": len(pending_down),
            "records": len(updated)}


class PsqlReplica:
    """psql uses externally configured libpq auth; never accepts a URL in argv."""

    def __init__(self, expected_database: str):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,62}", expected_database):
            raise ValueError("FEEDBACK_REPLICA_DATABASE_NAME_INVALID")
        self.expected_database = expected_database

    @staticmethod
    def _psql(sql: str) -> str:
        try:
            result = subprocess.run(
                ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
                input=sql, text=True, capture_output=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("FEEDBACK_REPLICA_CONNECTION_UNAVAILABLE") from exc
        if result.returncode != 0:
            # Do NOT expose stderr: Postgres error context can echo private rows.
            if "FEEDBACK_REPLICA_CONFLICT" in result.stderr:
                raise ValueError("FEEDBACK_REPLICA_CONFLICT")
            raise RuntimeError("FEEDBACK_REPLICA_SQL_FAILED")
        if len(result.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise RuntimeError("FEEDBACK_REPLICA_OUTPUT_TOO_LARGE")
        return result.stdout

    def _check_target(self) -> None:
        db = self._psql("SELECT current_database();").strip()
        if db != self.expected_database:
            raise RuntimeError("FEEDBACK_REPLICA_DATABASE_MISMATCH")
        table = self._psql(f"SELECT to_regclass('{TABLE}') IS NOT NULL;").strip()
        if table != "t":
            raise RuntimeError("FEEDBACK_REPLICA_TABLE_MISSING")

    def read(self) -> list[dict[str, Any]]:
        self._check_target()
        rows = self._psql(
            f"SELECT encode(convert_to(record::text,'UTF8'),'base64') "
            f"FROM {TABLE} ORDER BY feedback_ref LIMIT {MAX_RECORDS + 1};"
        )
        return decode_remote_records(rows)

    def append(self, records: list[dict[str, Any]]) -> None:
        if records:
            self._check_target()
            self._psql(sql_for_import(records))


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in local ↔ PostgreSQL feedback replica")
    parser.add_argument("--store-dir", type=pathlib.Path, required=True)
    parser.add_argument("--expected-database", required=True,
                        help="fail closed if the preauthorized libpq session reaches another DB")
    args = parser.parse_args()
    replica = PsqlReplica(args.expected_database)
    result = reconcile_feedback(args.store_dir, replica.read, replica.append)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
