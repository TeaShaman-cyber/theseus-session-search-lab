from __future__ import annotations

import re
import sqlite3

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _fts_or_query(tokens: list[str]) -> str:
    if not tokens:
        raise ValueError("LEXICAL_QUERY_EMPTY")
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def candidate_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    query_tokens: list[str],
    per_session_cap: int,
    candidate_budget: int,
) -> list[dict]:
    if not _TABLE_RE.fullmatch(table):
        raise ValueError("LEXICAL_TABLE_INVALID")
    if per_session_cap < 1:
        raise ValueError("PER_SESSION_CAP_INVALID")
    if candidate_budget < 1:
        raise ValueError("CANDIDATE_BUDGET_INVALID")

    match_query = _fts_or_query(query_tokens)
    sql = f"""
        WITH scored AS MATERIALIZED (
            SELECT
                d.session_id,
                d.ordinal,
                d.search_class,
                d.text_fp,
                bm25({table}) AS score
            FROM {table}
            JOIN docs d ON d.row_id={table}.rowid
            WHERE {table} MATCH ?
        ),
        ranked AS (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY session_id
                    ORDER BY score, ordinal
                ) AS session_rank
            FROM scored
        )
        SELECT *
        FROM ranked
        WHERE session_rank <= ?
        ORDER BY score, session_id, ordinal
        LIMIT ?
    """
    return [
        dict(row)
        for row in conn.execute(
            sql,
            [match_query, per_session_cap, candidate_budget],
        ).fetchall()
    ]


def target_rank(rows: list[dict], target_fp: str) -> int | None:
    for index, row in enumerate(rows, 1):
        if row["text_fp"] == target_fp:
            return index
    return None
