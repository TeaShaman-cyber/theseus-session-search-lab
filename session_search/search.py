from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3

from .corpus_store import CorpusPaths, resolve_corpus_root


def query_tokens(text: str) -> list[str]:
    return re.findall(r"[\w-]+", text.casefold(), flags=re.UNICODE)


def fts_query(text: str, *, operator: str = "AND") -> str:
    tokens = query_tokens(text)
    if not tokens:
        raise ValueError("query contains no searchable tokens")
    if operator not in {"AND", "OR"}:
        raise ValueError("unsupported FTS operator")
    return f" {operator} ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def rerank_recall_rows(rows: list[dict], query: str, limit: int) -> list[dict]:
    query_set = set(query_tokens(query))
    if not rows or not query_set:
        return rows[:limit]

    grouped: dict[str, dict] = {}
    for row in rows:
        overlap = query_set.intersection(query_tokens(row["text"]))
        session = grouped.setdefault(
            row["session_id"],
            {"rows": [], "covered": set(), "dialogue_hits": 0, "best_score": float("inf")},
        )
        session["covered"].update(overlap)
        if overlap and row["search_class"] == "dialogue":
            session["dialogue_hits"] += 1
        session["best_score"] = min(session["best_score"], row["score"])
        session["rows"].append((row, len(overlap)))

    sessions = sorted(
        grouped.items(),
        key=lambda item: (
            -(item[1]["dialogue_hits"] > 0),
            -len(item[1]["covered"]),
            -item[1]["dialogue_hits"],
            item[1]["best_score"],
            item[0],
        ),
    )

    ordered_sessions: list[list[dict]] = []
    for _session_id, data in sessions:
        session_rows = sorted(
            data["rows"],
            key=lambda item: (
                item[0]["search_class"] != "dialogue",
                -item[1],
                item[0]["score"],
                item[0]["ordinal"],
            ),
        )
        ordered_sessions.append([row for row, _overlap in session_rows])

    ranked: list[dict] = []
    round_index = 0
    while len(ranked) < limit:
        added = False
        for session_rows in ordered_sessions:
            if round_index < len(session_rows):
                ranked.append(session_rows[round_index])
                added = True
                if len(ranked) >= limit:
                    break
        if not added:
            break
        round_index += 1
    return ranked


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
    recall: bool = False,
) -> list[dict]:
    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    if not paths.db.exists():
        raise ValueError("CORPUS_PROJECTION_MISSING")
    conn = sqlite3.connect(paths.db)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in scopes)
        where = ["messages_fts MATCH ?", f"m.search_class IN ({placeholders})"]
        match_query = fts_query(query, operator="OR" if recall else "AND")
        params: list[object] = [match_query, *scopes]
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
        candidate_limit = limit * 3 if recall else limit
        params.append(candidate_limit)
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        if recall:
            return rerank_recall_rows(rows, query, limit)
        return rows
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
    parser.add_argument("--recall", action="store_true", help="Use broader session-level lexical recall ranking.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    scopes = args.scope or ["dialogue", "evidence"]

    if args.db:
        if args.session:
            parser.error("--session requires corpus search")
        if args.recall:
            parser.error("--recall requires corpus search")
        rows = search(args.db, args.query, scopes, args.limit)
        corpus_mode = False
    else:
        try:
            corpus_root = resolve_corpus_root(args.corpus)
        except ValueError as exc:
            parser.error(str(exc))
        rows = search_corpus(
            corpus_root,
            args.query,
            scopes,
            args.limit,
            session_id=args.session,
            recall=args.recall,
        )
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
