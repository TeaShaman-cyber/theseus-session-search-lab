#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import statistics

from session_search.corpus_lock import resolve_corpus_lock
from session_search.search import fts_query, rerank_recall_rows, search_corpus

SCOPES = ["dialogue", "evidence"]


def content_fp(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def identity(row: dict) -> tuple[str, int, str | None]:
    return (row["session_id"], row["ordinal"], row.get("message_id"))


def group_exact_text(rows: list[dict]) -> list[dict]:
    groups: list[dict] = []
    by_text: dict[str, dict] = {}
    for row in rows:
        text = row["text"]
        group = by_text.get(text)
        if group is None:
            group = {
                "text": text,
                "score": row["score"],
                "representative": identity(row),
                "occurrences": [],
            }
            by_text[text] = group
            groups.append(group)
        group["occurrences"].append(
            [row["session_id"], row.get("message_id"), row["ordinal"]]
        )
    return groups


def compare_topk(rows: list[dict], *, target_text: str, k: int) -> dict:
    if k < 1:
        raise ValueError("k must be positive")
    baseline = rows[:k]
    groups = group_exact_text(rows)
    selected = groups[:k]

    baseline_rank = next(
        (i for i, row in enumerate(baseline, 1) if row["text"] == target_text),
        None,
    )
    grouped_rank = next(
        (i for i, group in enumerate(selected, 1) if group["text"] == target_text),
        None,
    )
    return {
        "baseline_target_rank": baseline_rank,
        "grouped_target_rank": grouped_rank,
        "baseline_unique_texts": len({row["text"] for row in baseline}),
        "grouped_unique_texts": len(selected),
    }


def _fixed_recall_rows(
    conn: sqlite3.Connection,
    *,
    query: str,
    candidate_budget: int,
) -> list[dict]:
    placeholders = ",".join("?" for _ in SCOPES)
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
            WHERE messages_fts MATCH ?
              AND m.search_class IN ({placeholders})
        )
        WHERE session_rank <= 3
        ORDER BY score, session_id, ordinal
        LIMIT ?
    """
    params = [fts_query(query, operator="OR"), *SCOPES, candidate_budget]
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    for row in rows:
        row.pop("session_rank", None)
    return rerank_recall_rows(rows, query, candidate_budget)


def _rank_target(rows: list[dict], target_fp: str) -> int | None:
    for index, row in enumerate(rows, 1):
        if content_fp(row["text"]) == target_fp:
            return index
    return None


def _rank_target_groups(groups: list[dict], target_fp: str) -> int | None:
    for index, group in enumerate(groups, 1):
        if content_fp(group["text"]) == target_fp:
            return index
    return None


def _metrics(ranks: list[int | None]) -> dict:
    count = len(ranks)
    return {
        "count": count,
        "r_at_1": sum(r is not None and r <= 1 for r in ranks) / count,
        "r_at_3": sum(r is not None and r <= 3 for r in ranks) / count,
        "r_at_5": sum(r is not None and r <= 5 for r in ranks) / count,
        "r_at_10": sum(r is not None and r <= 10 for r in ranks) / count,
        "mrr": sum(1 / r for r in ranks if r is not None) / count,
    }


def run(corpus: pathlib.Path, gold_file: pathlib.Path, *, k: int, candidate_budget: int) -> dict:
    if k < 1 or candidate_budget < k:
        raise ValueError("candidate_budget must be >= k >= 1")

    gold = json.loads(gold_file.read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{corpus.resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    cases = []
    parity_count = 0
    try:
        for item in gold:
            ordered = _fixed_recall_rows(
                conn, query=item["query_text"], candidate_budget=candidate_budget
            )
            baseline = ordered[:k]
            groups = group_exact_text(ordered)
            grouped = groups[:k]

            current = search_corpus(
                corpus.parent,
                item["query_text"],
                SCOPES,
                k,
                recall=True,
            )
            if [identity(row) for row in current] == [identity(row) for row in baseline]:
                parity_count += 1

            baseline_rank = _rank_target(baseline, item["target_content_fp"])
            grouped_rank = _rank_target_groups(grouped, item["target_content_fp"])
            cases.append(
                {
                    "case_id": item["case_id"],
                    "query_lang": item["query_lang"],
                    "baseline_rank": baseline_rank,
                    "grouped_rank": grouped_rank,
                    "baseline_unique_top_k": len({row["text"] for row in baseline}),
                    "grouped_unique_top_k": len(grouped),
                    "candidate_rows": len(ordered),
                    "candidate_occurrences_preserved": (
                        sum(len(group["occurrences"]) for group in groups) == len(ordered)
                    ),
                }
            )
    finally:
        conn.close()

    def summarize(lang: str) -> dict:
        subset = cases if lang == "all" else [c for c in cases if c["query_lang"] == lang]
        baseline = [c["baseline_rank"] for c in subset]
        grouped = [c["grouped_rank"] for c in subset]

        def improved(a, b):
            return (a is None and b is not None) or (
                a is not None and b is not None and b < a
            )

        def worse(a, b):
            return (b is None and a is not None) or (
                a is not None and b is not None and b > a
            )

        return {
            "baseline": _metrics(baseline),
            "exact_grouped": _metrics(grouped),
            "mean_unique_top_k_baseline": statistics.fmean(
                c["baseline_unique_top_k"] for c in subset
            ),
            "mean_unique_top_k_grouped": statistics.fmean(
                c["grouped_unique_top_k"] for c in subset
            ),
            "improved": sum(improved(a, b) for a, b in zip(baseline, grouped)),
            "same": sum(a == b for a, b in zip(baseline, grouped)),
            "worse": sum(worse(a, b) for a, b in zip(baseline, grouped)),
        }

    public_cases = [
        {
            "case_id": case["case_id"],
            "query_lang": case["query_lang"],
            "baseline_rank": case["baseline_rank"],
            "grouped_rank": case["grouped_rank"],
            "baseline_unique_top_k": case["baseline_unique_top_k"],
            "grouped_unique_top_k": case["grouped_unique_top_k"],
            "candidate_rows": case["candidate_rows"],
            "candidate_occurrences_preserved": case[
                "candidate_occurrences_preserved"
            ],
        }
        for case in cases
    ]
    return {
        "schema": "theseus.session-search-exact-grouping-benchmark.v1",
        "k": k,
        "candidate_budget": candidate_budget,
        "current_recall_prefix_parity": {
            "matching_cases": parity_count,
            "total_cases": len(cases),
        },
        "summary": {
            "ru": summarize("ru"),
            "en": summarize("en"),
            "all": summarize("all"),
        },
        "cases": public_cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-lock", type=pathlib.Path, required=True)
    parser.add_argument("--gold", type=pathlib.Path, required=True)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--candidate-budget", type=int, default=30)
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args()
    corpus = resolve_corpus_lock(args.corpus_lock)
    result = run(
        corpus,
        args.gold,
        k=args.k,
        candidate_budget=args.candidate_budget,
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
