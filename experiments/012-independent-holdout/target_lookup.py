#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3

from session_search.corpus_lock import resolve_corpus_lock
from session_search.search import fts_query


def lookup(
    conn: sqlite3.Connection,
    *,
    query: str,
    before_time: float,
    limit: int,
) -> list[dict]:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("LIMIT_INVALID")
    sql = """
        SELECT
            m.session_id,
            m.ordinal,
            m.message_id,
            m.role,
            m.search_class,
            m.create_time,
            m.text,
            bm25(messages_fts) AS score
        FROM messages_fts
        JOIN messages m ON m.rowid=messages_fts.rowid
        WHERE messages_fts MATCH ?
          AND m.create_time < ?
          AND m.search_class IN ('dialogue','evidence')
        ORDER BY score,m.create_time,m.session_id,m.ordinal
        LIMIT ?
    """
    return [
        dict(row)
        for row in conn.execute(sql, [fts_query(query), before_time, limit]).fetchall()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-lock", type=pathlib.Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--before-time", type=float, required=True)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()

    db = resolve_corpus_lock(args.corpus_lock)
    conn = sqlite3.connect(f"file:{db.resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = lookup(
            conn,
            query=args.query,
            before_time=args.before_time,
            limit=args.limit,
        )
    finally:
        conn.close()

    print(json.dumps(rows, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
