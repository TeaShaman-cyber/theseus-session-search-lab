#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3

from session_search.shard_allocation import (
    DEFAULT_TARGET_BYTES,
    HARD_LIMIT_BYTES,
    allocate_sessions,
    load_session_rows,
    public_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan provider-aware Neon shards from a read-only Session Search projection."
    )
    parser.add_argument("--database", required=True, type=pathlib.Path)
    parser.add_argument("--target-mib", type=int, default=700)
    parser.add_argument("--hard-limit-bytes", type=int, default=HARD_LIMIT_BYTES)
    args = parser.parse_args()

    db = args.database.resolve()
    if not db.is_file():
        raise SystemExit(f"database not found: {db}")
    target_bytes = args.target_mib * 1024 * 1024
    if args.target_mib == 700:
        target_bytes = DEFAULT_TARGET_BYTES

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = load_session_rows(conn)
    finally:
        conn.close()

    allocation = allocate_sessions(
        rows,
        target_bytes=target_bytes,
        hard_limit_bytes=args.hard_limit_bytes,
    )
    print(json.dumps(public_summary(allocation), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
