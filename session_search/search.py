from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3

from .corpus_store import CorpusPaths, resolve_corpus_root


def fts_query(text: str) -> str:
    tokens = re.findall(r"[\w-]+", text, flags=re.UNICODE)
    if not tokens:
        raise ValueError("query contains no searchable tokens")
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def search(db: str, query: str, scopes: list[str], limit: int) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in scopes)
        sql = f"""
            SELECT m.ordinal,m.message_id,m.role,m.content_type,m.search_class,m.text,bm25(messages_fts) AS score
            FROM messages_fts
            JOIN messages m ON m.row_id=messages_fts.rowid
            WHERE messages_fts MATCH ? AND m.search_class IN ({placeholders})
            ORDER BY score, m.ordinal
            LIMIT ?
        """
        rows = conn.execute(sql, [fts_query(query), *scopes, limit]).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def search_corpus(
    corpus_root: pathlib.Path,
    query: str,
    scopes: list[str],
    limit: int,
    session_id: str | None = None,
) -> list[dict]:
    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    if not paths.db.exists():
        raise ValueError("CORPUS_PROJECTION_MISSING")
    conn = sqlite3.connect(paths.db)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in scopes)
        where = ["messages_fts MATCH ?", f"m.search_class IN ({placeholders})"]
        params: list[object] = [fts_query(query), *scopes]
        if session_id is not None:
            where.append("m.session_id=?")
            params.append(session_id)
        sql = f"""
            SELECT
                m.session_id,
                s.title AS session_title,
                s.coverage_state AS session_coverage,
                m.ordinal,
                m.message_id,
                m.role,
                m.content_type,
                m.search_class,
                m.create_time,
                m.text,
                bm25(messages_fts) AS score
            FROM messages_fts
            JOIN messages m ON m.row_id=messages_fts.rowid
            JOIN sessions s ON s.session_id=m.session_id
            WHERE {' AND '.join(where)}
            ORDER BY score, m.session_id, m.ordinal
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search a regeneratable session-history FTS projection.")
    parser.add_argument("query")
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--db")
    location.add_argument("--corpus")
    parser.add_argument("--session")
    parser.add_argument("--scope", action="append", choices=["dialogue", "evidence", "trace"])
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    scopes = args.scope or ["dialogue", "evidence"]

    if args.db:
        if args.session:
            parser.error("--session requires corpus search")
        rows = search(args.db, args.query, scopes, args.limit)
        corpus_mode = False
    else:
        try:
            corpus_root = resolve_corpus_root(args.corpus)
        except ValueError as exc:
            parser.error(str(exc))
        rows = search_corpus(corpus_root, args.query, scopes, args.limit, session_id=args.session)
        corpus_mode = True

    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for i, row in enumerate(rows, 1):
            if corpus_mode:
                print(
                    f"=== HIT {i} session={row['session_id']} coverage={row['session_coverage']} "
                    f"scope={row['search_class']} ordinal={row['ordinal']} role={row['role']} ==="
                )
            else:
                print(
                    f"=== HIT {i} scope={row['search_class']} ordinal={row['ordinal']} "
                    f"role={row['role']} ==="
                )
            print(row["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
