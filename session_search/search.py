from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3
import unicodedata

from .corpus_store import CorpusPaths, resolve_corpus_root


def fts_tokens(text: str) -> list[str]:
    return re.findall(r"[\w-]+", text, flags=re.UNICODE)


def query_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFD", text.casefold())
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)


def fts_query(text: str, *, operator: str = "AND") -> str:
    tokens = fts_tokens(text)
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
            -len(item[1]["covered"]),
            -(item[1]["dialogue_hits"] > 0),
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


def _bounded_candidate_budget(result_limit: int, candidate_budget: int | None = None) -> int:
    if not isinstance(result_limit, int) or isinstance(result_limit, bool) or result_limit < 1 or result_limit > 100:
        raise ValueError("RESULT_LIMIT_OUT_OF_BOUNDS")
    if candidate_budget is None:
        candidate_budget = min(300, result_limit * 3)
    if not isinstance(candidate_budget, int) or isinstance(candidate_budget, bool):
        raise ValueError("CANDIDATE_BUDGET_OUT_OF_BOUNDS")
    if candidate_budget < result_limit:
        raise ValueError("CANDIDATE_BUDGET_BELOW_RESULT_LIMIT")
    if candidate_budget > 300:
        raise ValueError("CANDIDATE_BUDGET_OUT_OF_BOUNDS")
    return candidate_budget


def search_flat(db: str, query: str, scopes: list[str], limit: int) -> list[dict]:
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


def search(
    db: str,
    query: str,
    scopes: list[str],
    limit: int,
    *,
    candidate_budget: int | None = None,
) -> list[dict]:
    budget = _bounded_candidate_budget(limit, candidate_budget)
    return group_exact_search_rows(search_flat(db, query, scopes, budget))[:limit]



_OCCURRENCE_FIELDS = (
    "session_id",
    "message_id",
    "ordinal",
    "session_title",
    "session_coverage",
    "role",
    "search_class",
    "create_time",
    "provider",
    "shard_id",
    "generation_id",
    "shard_rank",
)


def group_exact_search_rows(rows: list[dict]) -> list[dict]:
    groups: list[dict] = []
    by_text: dict[str, dict] = {}
    for row in rows:
        text = row.get("text")
        if not isinstance(text, str):
            raise ValueError("GROUP_TEXT_INVALID")
        group = by_text.get(text)
        if group is None:
            group = {
                "text": text,
                "representative": {key: value for key, value in row.items() if key != "text"},
                "occurrence_count": 0,
                "occurrences": [],
            }
            by_text[text] = group
            groups.append(group)
        group["occurrences"].append(
            {key: row[key] for key in _OCCURRENCE_FIELDS if key in row}
        )
        group["occurrence_count"] = len(group["occurrences"])
    return groups


def _strict_candidate_rows(
    conn: sqlite3.Connection,
    query: str,
    scopes: list[str],
    session_id: str | None,
    *,
    candidate_budget: int,
) -> list[dict]:
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
    params.append(candidate_budget)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _recall_candidate_rows(
    conn: sqlite3.Connection,
    query: str,
    scopes: list[str],
    result_limit: int,
    session_id: str | None,
    *,
    candidate_budget: int | None = None,
) -> list[dict]:
    placeholders = ",".join("?" for _ in scopes)
    where = ["messages_fts MATCH ?", f"m.search_class IN ({placeholders})"]
    params: list[object] = [fts_query(query, operator="OR"), *scopes]
    if session_id is not None:
        where.append("m.session_id=?")
        params.append(session_id)

    if candidate_budget is None:
        candidate_budget = min(300, result_limit * 3)
    if not isinstance(candidate_budget, int) or isinstance(candidate_budget, bool):
        raise ValueError("CANDIDATE_BUDGET_OUT_OF_BOUNDS")
    if candidate_budget < result_limit:
        raise ValueError("CANDIDATE_BUDGET_BELOW_RESULT_LIMIT")
    if candidate_budget > 300:
        raise ValueError("CANDIDATE_BUDGET_OUT_OF_BOUNDS")

    per_session_limit = result_limit if session_id is not None else min(3, result_limit)
    sql = f"""
        SELECT * FROM (
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
                messages_fts.rank AS score,
                row_number() OVER (
                    PARTITION BY m.session_id
                    ORDER BY messages_fts.rank, m.ordinal
                ) AS session_rank
            FROM messages_fts
            JOIN messages m ON m.row_id=messages_fts.rowid
            JOIN sessions s ON s.session_id=m.session_id
            WHERE {' AND '.join(where)}
        )
        WHERE session_rank <= ?
        ORDER BY score, session_id, ordinal
        LIMIT ?
    """
    params.extend([per_session_limit, candidate_budget])
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    for row in rows:
        row.pop("session_rank", None)
    return rerank_recall_rows(rows, query, candidate_budget)


def search_corpus_grouped_recall(
    corpus_root: pathlib.Path,
    query: str,
    scopes: list[str],
    limit: int,
    session_id: str | None = None,
    *,
    candidate_budget: int | None = None,
) -> list[dict]:
    return search_corpus(
        corpus_root,
        query,
        scopes,
        limit,
        session_id=session_id,
        recall=True,
        candidate_budget=candidate_budget,
    )


def search_corpus_flat(
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
        if recall:
            return _recall_candidate_rows(
                conn,
                query,
                scopes,
                limit,
                session_id,
                candidate_budget=min(300, limit * 3),
            )[:limit]
        return _strict_candidate_rows(
            conn, query, scopes, session_id, candidate_budget=limit
        )
    finally:
        conn.close()


def search_corpus(
    corpus_root: pathlib.Path,
    query: str,
    scopes: list[str],
    limit: int,
    session_id: str | None = None,
    recall: bool = False,
    *,
    candidate_budget: int | None = None,
) -> list[dict]:
    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    if not paths.db.exists():
        raise ValueError("CORPUS_PROJECTION_MISSING")
    budget = _bounded_candidate_budget(limit, candidate_budget)
    conn = sqlite3.connect(paths.db)
    conn.row_factory = sqlite3.Row
    try:
        if recall:
            candidates = _recall_candidate_rows(
                conn, query, scopes, limit, session_id, candidate_budget=budget
            )
        else:
            candidates = _strict_candidate_rows(
                conn, query, scopes, session_id, candidate_budget=budget
            )
        return group_exact_search_rows(candidates)[:limit]
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
    parser.add_argument(
        "--reconcile-registry",
        type=pathlib.Path,
        help="Opt into local controlled-label/alias reconciliation using a JSON registry.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    scopes = args.scope or ["dialogue", "evidence"]

    reconciliation_result = None
    if args.db:
        if args.session:
            parser.error("--session requires corpus search")
        if args.recall:
            parser.error("--recall requires corpus search")
        if args.reconcile_registry:
            parser.error("--reconcile-registry requires corpus search")
        rows = search(args.db, args.query, scopes, args.limit)
        corpus_mode = False
    else:
        try:
            corpus_root = resolve_corpus_root(args.corpus)
        except ValueError as exc:
            parser.error(str(exc))
        if args.reconcile_registry:
            from .reconciliation import load_concept_registry, reconcile_search

            try:
                registry = load_concept_registry(args.reconcile_registry)
                reconciliation_result = reconcile_search(
                    corpus_root,
                    args.query,
                    scopes,
                    args.limit,
                    registry,
                    session_id=args.session,
                    recall=args.recall,
                )
            except ValueError as exc:
                parser.error(str(exc))
            rows = reconciliation_result["hits"]
        else:
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
        print(json.dumps(reconciliation_result if reconciliation_result is not None else rows, ensure_ascii=False))
    else:
        if reconciliation_result is not None:
            print(
                f"=== RECONCILIATION state={reconciliation_result['state']} "
                f"promotion={reconciliation_result['promotion_eligibility']} "
                f"mode={reconciliation_result['query_mode']} ==="
            )
        for i, row in enumerate(rows, 1):
            if reconciliation_result is None:
                representative = row["representative"]
                session = representative.get("session_id", "legacy")
                coverage = representative.get("session_coverage", "UNKNOWN")
                scope = representative.get("search_class", "unknown")
                ordinal = representative.get("ordinal", "?")
                role = representative.get("role", "unknown")
                print(
                    f"=== RESULT {i} occurrences={row['occurrence_count']} session={session} "
                    f"coverage={coverage} scope={scope} ordinal={ordinal} role={role} ==="
                )
                print(row["text"])
                continue

            suffix = (
                f" basis={row['retrieval_basis']} "
                f"promotion={row['promotion_eligibility']}"
            )
            print(
                f"=== HIT {i} session={row['session_id']} coverage={row['session_coverage']} "
                f"scope={row['search_class']} ordinal={row['ordinal']} role={row['role']}{suffix} ==="
            )
            print(row["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
