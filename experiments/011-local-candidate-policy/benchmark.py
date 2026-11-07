#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import statistics
import time

from session_search.search import _recall_candidate_rows, fts_query, rerank_recall_rows

SCOPES = ['dialogue', 'evidence']
DEFAULT_POLICIES = [
    (3, 30),
    (3, 60),
    (3, 120),
    (3, 300),
    (5, 30),
    (10, 30),
    (30, 30),
    (10, 120),
    (30, 300),
]


def validate_policy(per_session_cap: int, candidate_budget: int) -> None:
    if not isinstance(per_session_cap, int) or isinstance(per_session_cap, bool) or per_session_cap < 1:
        raise ValueError('PER_SESSION_CAP_INVALID')
    if not isinstance(candidate_budget, int) or isinstance(candidate_budget, bool) or candidate_budget < 1:
        raise ValueError('CANDIDATE_BUDGET_INVALID')


def content_fp(text: str) -> str:
    normalized = re.sub(r'\s+', ' ', text.strip().lower())
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()


def candidate_rows(
    conn: sqlite3.Connection,
    *,
    query: str,
    scopes: list[str],
    per_session_cap: int,
    candidate_budget: int,
) -> list[dict]:
    validate_policy(per_session_cap, candidate_budget)
    placeholders = ','.join('?' for _ in scopes)
    sql = f'''
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
        WHERE session_rank <= ?
        ORDER BY score, session_id, ordinal
        LIMIT ?
    '''
    params = [
        fts_query(query, operator='OR'),
        *scopes,
        per_session_cap,
        candidate_budget,
    ]
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    for row in rows:
        row.pop('session_rank', None)
    return rerank_recall_rows(rows, query, candidate_budget)


def target_rank(rows: list[dict], target_fp: str) -> int | None:
    for index, row in enumerate(rows, 1):
        if content_fp(row['text']) == target_fp:
            return index
    return None


def public_case(item: dict, *, rank: int | None) -> dict:
    return {
        "case_id": item["case_id"],
        "query_lang": item["query_lang"],
        "target_rank": rank,
    }


def compare_cases(baseline: list[dict], candidate: list[dict], *, k: int) -> dict:
    base = {(row["case_id"], row["query_lang"]): row["target_rank"] for row in baseline}
    cand = {(row["case_id"], row["query_lang"]): row["target_rank"] for row in candidate}
    if set(base) != set(cand):
        raise ValueError("CASE_SET_MISMATCH")

    out = {
        "reach_gained": 0, "reach_lost": 0,
        "topk_gained": 0, "topk_lost": 0,
        "rank_improved": 0, "rank_same": 0, "rank_worse": 0,
    }
    for key in sorted(base):
        a, b = base[key], cand[key]
        if a is None and b is not None:
            out["reach_gained"] += 1
        if a is not None and b is None:
            out["reach_lost"] += 1
        if not (a is not None and a <= k) and (b is not None and b <= k):
            out["topk_gained"] += 1
        if (a is not None and a <= k) and not (b is not None and b <= k):
            out["topk_lost"] += 1
        if (a is None and b is not None) or (a is not None and b is not None and b < a):
            out["rank_improved"] += 1
        elif a == b:
            out["rank_same"] += 1
        else:
            out["rank_worse"] += 1
    return out


def summarize(ranks: list[int | None], *, k: int) -> dict:
    count = len(ranks)
    reachable = sum(rank is not None for rank in ranks)
    topk = sum(rank is not None and rank <= k for rank in ranks)
    return {
        'count': count,
        'candidate_reachable': reachable,
        'candidate_recall': reachable / count,
        'topk_hits': topk,
        'r_at_k': topk / count,
        'reachable_to_topk': topk / reachable if reachable else 0.0,
        'mrr': sum(1.0 / rank for rank in ranks if rank is not None) / count,
    }


def run(
    corpus: pathlib.Path,
    gold_file: pathlib.Path,
    *,
    k: int = 10,
    policies: list[tuple[int, int]] | None = None,
) -> dict:
    gold = json.loads(gold_file.read_text(encoding='utf-8'))
    policies = policies or DEFAULT_POLICIES
    conn = sqlite3.connect(f'file:{corpus.resolve()}?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    results = []
    baseline_parity = 0
    try:
        for per_session_cap, candidate_budget in policies:
            validate_policy(per_session_cap, candidate_budget)
            ranks: list[int | None] = []
            public_cases: list[dict] = []
            timings: list[float] = []
            for item in gold:
                started = time.perf_counter()
                rows = candidate_rows(
                    conn,
                    query=item['query_text'],
                    scopes=SCOPES,
                    per_session_cap=per_session_cap,
                    candidate_budget=candidate_budget,
                )
                timings.append((time.perf_counter() - started) * 1000.0)
                rank = target_rank(rows, item['target_content_fp'])
                ranks.append(rank)
                public_cases.append(public_case(item, rank=rank))

                if (per_session_cap, candidate_budget) == (3, 30):
                    expected = _recall_candidate_rows(conn, item['query_text'], SCOPES, k, None)
                    left = [(r['session_id'], r['ordinal'], r.get('message_id')) for r in rows]
                    right = [(r['session_id'], r['ordinal'], r.get('message_id')) for r in expected]
                    if left == right:
                        baseline_parity += 1

            metrics = summarize(ranks, k=k)
            timings_sorted = sorted(timings)
            p95_index = min(len(timings_sorted) - 1, int(len(timings_sorted) * 0.95))
            results.append({
                'per_session_cap': per_session_cap,
                'candidate_budget': candidate_budget,
                **metrics,
                'latency_ms_mean': statistics.fmean(timings),
                'latency_ms_p50': statistics.median(timings),
                'latency_ms_p95': timings_sorted[p95_index],
                'cases': public_cases,
            })
    finally:
        conn.close()

    baseline = next(
        item for item in results
        if item['per_session_cap'] == 3 and item['candidate_budget'] == 30
    )
    for item in results:
        item['vs_current'] = compare_cases(baseline['cases'], item['cases'], k=k)

    return {
        'schema': 'theseus.session-search-local-candidate-policy.v1',
        'k': k,
        'gold_rows': len(gold),
        'current_policy_parity': {
            'matching_cases': baseline_parity,
            'total_cases': len(gold),
        },
        'policies': results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--corpus', type=pathlib.Path, required=True)
    parser.add_argument('--gold', type=pathlib.Path, required=True)
    parser.add_argument('--out', type=pathlib.Path)
    args = parser.parse_args()
    corpus = args.corpus / 'corpus.sqlite3' if args.corpus.is_dir() else args.corpus
    result = run(corpus, args.gold)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if args.out:
        args.out.write_text(payload, encoding='utf-8')
    print(payload, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
