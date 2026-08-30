from __future__ import annotations

import argparse
import json
import re
import sqlite3


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Search a regeneratable session-history FTS projection.")
    p.add_argument("query")
    p.add_argument("--db", required=True)
    p.add_argument("--scope", action="append", choices=["dialogue", "evidence", "trace"])
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    scopes = args.scope or ["dialogue", "evidence"]
    rows = search(args.db, args.query, scopes, args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
    else:
        for i, row in enumerate(rows, 1):
            print(f"=== HIT {i} scope={row['search_class']} ordinal={row['ordinal']} role={row['role']} ===")
            print(row["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
