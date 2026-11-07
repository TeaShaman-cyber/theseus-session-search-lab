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
from collections import Counter

from session_search.search import fts_query, rerank_recall_rows

SCOPES = ['dialogue', 'evidence']


def cap_domain(candidate_budget: int) -> list[int]:
    if not isinstance(candidate_budget, int) or isinstance(candidate_budget, bool) or candidate_budget < 1:
        raise ValueError('BUDGET_INVALID')
    return list(range(1, candidate_budget + 1))


def content_fp(text: str) -> str:
    normalized = re.sub(r'\s+', ' ', text.strip().lower())
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()


def raw_evidence(
    conn: sqlite3.Connection,
    *,
    query: str,
    scopes: list[str],
    max_per_session: int,
) -> list[dict]:
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
        ORDER BY score,session_id,ordinal
    '''
    params = [fts_query(query, operator='OR'), *scopes, max_per_session]
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _bounded_rows(evidence: list[dict], *, per_session_cap: int, candidate_budget: int) -> list[dict]:
    if per_session_cap < 1:
        raise ValueError('PER_SESSION_CAP_INVALID')
    if candidate_budget < 1:
        raise ValueError('CANDIDATE_BUDGET_INVALID')
    rows = [row for row in evidence if int(row['session_rank']) <= per_session_cap]
    return rows[:candidate_budget]


def apply_policy(
    evidence: list[dict],
    *,
    query: str,
    per_session_cap: int,
    candidate_budget: int,
) -> list[dict]:
    rows = []
    for row in _bounded_rows(evidence, per_session_cap=per_session_cap, candidate_budget=candidate_budget):
        clean = dict(row)
        clean.pop('session_rank', None)
        rows.append(clean)
    return rerank_recall_rows(rows, query, candidate_budget)


def direct_candidate_rows(
    conn: sqlite3.Connection,
    *,
    query: str,
    scopes: list[str],
    per_session_cap: int,
    candidate_budget: int,
) -> list[dict]:
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
        ORDER BY score,session_id,ordinal
        LIMIT ?
    '''
    params = [fts_query(query, operator='OR'), *scopes, per_session_cap, candidate_budget]
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    for row in rows:
        row.pop('session_rank', None)
    return rerank_recall_rows(rows, query, candidate_budget)


def concentration(rows: list[dict]) -> dict:
    if not rows:
        return {'candidate_count': 0, 'unique_sessions': 0, 'max_session_share': 0.0}
    counts = Counter(row['session_id'] for row in rows)
    total = len(rows)
    return {
        'candidate_count': total,
        'unique_sessions': len(counts),
        'max_session_share': max(counts.values()) / total,
    }


def target_rank(rows: list[dict], target_fp: str) -> int | None:
    for index, row in enumerate(rows, 1):
        if content_fp(row['text']) == target_fp:
            return index
    return None


def summarize_case_rows(case_rows: list[dict], *, k: int) -> dict:
    count = len(case_rows)
    reachable = sum(row['target_rank'] is not None for row in case_rows)
    topk = sum(row['target_rank'] is not None and row['target_rank'] <= k for row in case_rows)
    return {
        'count': count,
        'candidate_reachable': reachable,
        'candidate_recall': reachable / count,
        'topk_hits': topk,
        'r_at_k': topk / count,
        'mrr': sum((1.0 / row['target_rank']) if row['target_rank'] else 0.0 for row in case_rows) / count,
        'mean_candidate_count': sum(row['candidate_count'] for row in case_rows) / count,
        'mean_unique_sessions': sum(row['unique_sessions'] for row in case_rows) / count,
        'mean_max_session_share': sum(row['max_session_share'] for row in case_rows) / count,
        'mean_topk_unique_sessions': sum(row['topk_unique_sessions'] for row in case_rows) / count,
    }


def compare_to_current(current_cases: list[dict], candidate_cases: list[dict], *, k: int) -> dict:
    base = {(r['case_id'], r['query_lang']): r['target_rank'] for r in current_cases}
    cand = {(r['case_id'], r['query_lang']): r['target_rank'] for r in candidate_cases}
    if set(base) != set(cand):
        raise ValueError('CASE_SET_MISMATCH')
    out = {'topk_gained': 0, 'topk_lost': 0, 'rank_improved': 0, 'rank_same': 0, 'rank_worse': 0}
    for key in sorted(base):
        a, b = base[key], cand[key]
        if not (a is not None and a <= k) and (b is not None and b <= k):
            out['topk_gained'] += 1
        if (a is not None and a <= k) and not (b is not None and b <= k):
            out['topk_lost'] += 1
        if (a is None and b is not None) or (a is not None and b is not None and b < a):
            out['rank_improved'] += 1
        elif a == b:
            out['rank_same'] += 1
        else:
            out['rank_worse'] += 1
    return out


def select_challenger(points: list[dict], *, current_cap: int = 3) -> dict:
    current = next(point for point in points if point['per_session_cap'] == current_cap)
    eligible = [point for point in points if point['vs_current']['topk_lost'] == 0]
    best_r = max(point['r_at_k'] for point in eligible)
    eligible = [point for point in eligible if point['r_at_k'] == best_r]
    best_recall = max(point['candidate_recall'] for point in eligible)
    eligible = [point for point in eligible if point['candidate_recall'] == best_recall]
    selected = min(eligible, key=lambda point: point['per_session_cap'])
    return {
        'current_cap': current_cap,
        'selected_cap': selected['per_session_cap'],
        'current_r_at_k': current['r_at_k'],
        'selected_r_at_k': selected['r_at_k'],
        'current_candidate_recall': current['candidate_recall'],
        'selected_candidate_recall': selected['candidate_recall'],
        'selection_rule': 'no_topk_loss_then_max_r_at_k_then_max_candidate_recall_then_min_cap',
    }


def latency_smoke(
    conn: sqlite3.Connection,
    gold: list[dict],
    *,
    per_session_cap: int,
    candidate_budget: int,
) -> dict:
    timings = []
    for item in gold:
        started = time.perf_counter()
        direct_candidate_rows(
            conn,
            query=item['query_text'],
            scopes=SCOPES,
            per_session_cap=per_session_cap,
            candidate_budget=candidate_budget,
        )
        timings.append((time.perf_counter() - started) * 1000.0)
    ordered = sorted(timings)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        'queries': len(timings),
        'mean_ms': statistics.fmean(timings),
        'p50_ms': statistics.median(timings),
        'p95_ms': ordered[p95_index],
    }


def run(corpus: pathlib.Path, gold_file: pathlib.Path, *, k: int = 10, candidate_budget: int = 30) -> dict:
    gold = json.loads(gold_file.read_text(encoding='utf-8'))
    conn = sqlite3.connect(f'file:{corpus.resolve()}?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    evidence_by_case = {}
    production_parity = 0
    try:
        for item in gold:
            key = (item['case_id'], item['query_lang'])
            evidence = raw_evidence(conn, query=item['query_text'], scopes=SCOPES, max_per_session=candidate_budget)
            evidence_by_case[key] = evidence
            actual = apply_policy(evidence, query=item['query_text'], per_session_cap=3, candidate_budget=candidate_budget)
            from session_search.search import _recall_candidate_rows
            expected = _recall_candidate_rows(conn, item['query_text'], SCOPES, k, None)
            left = [(r['session_id'], r['ordinal'], r.get('message_id')) for r in actual]
            right = [(r['session_id'], r['ordinal'], r.get('message_id')) for r in expected]
            production_parity += int(left == right)

        points = []
        cases_by_cap = {}
        for cap in cap_domain(candidate_budget):
            case_rows = []
            for item in gold:
                evidence = evidence_by_case[(item['case_id'], item['query_lang'])]
                bounded = _bounded_rows(evidence, per_session_cap=cap, candidate_budget=candidate_budget)
                ranked = apply_policy(evidence, query=item['query_text'], per_session_cap=cap, candidate_budget=candidate_budget)
                conc = concentration(bounded)
                rank = target_rank(ranked, item['target_content_fp'])
                case_rows.append({
                    'case_id': item['case_id'],
                    'query_lang': item['query_lang'],
                    'target_rank': rank,
                    **conc,
                    'topk_unique_sessions': len({row['session_id'] for row in ranked[:k]}),
                })
            cases_by_cap[cap] = case_rows
            point = {'per_session_cap': cap, **summarize_case_rows(case_rows, k=k)}
            points.append(point)

        current_cases = cases_by_cap[3]
        previous = None
        for point in points:
            point['vs_current'] = compare_to_current(current_cases, cases_by_cap[point['per_session_cap']], k=k)
            if previous is None:
                point['marginal'] = None
            else:
                point['marginal'] = {
                    'delta_candidate_recall': point['candidate_recall'] - previous['candidate_recall'],
                    'delta_r_at_k': point['r_at_k'] - previous['r_at_k'],
                    'delta_mrr': point['mrr'] - previous['mrr'],
                    'delta_mean_max_session_share': point['mean_max_session_share'] - previous['mean_max_session_share'],
                }
            previous = point

        selection = select_challenger(points)
        selection['latency_smoke'] = {
            'current': latency_smoke(
                conn,
                gold,
                per_session_cap=selection['current_cap'],
                candidate_budget=candidate_budget,
            ),
            'selected': latency_smoke(
                conn,
                gold,
                per_session_cap=selection['selected_cap'],
                candidate_budget=candidate_budget,
            ),
        }
        return {
            'schema': 'theseus.session-search-per-session-cap-curve.v1',
            'k': k,
            'candidate_budget': candidate_budget,
            'gold_rows': len(gold),
            'production_parity': {'matching_cases': production_parity, 'total_cases': len(gold)},
            'selection': selection,
            'points': points,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--corpus', type=pathlib.Path, required=True)
    parser.add_argument('--gold', type=pathlib.Path, required=True)
    parser.add_argument('--out', type=pathlib.Path, required=True)
    args = parser.parse_args()
    db = args.corpus / 'corpus.sqlite3' if args.corpus.is_dir() else args.corpus
    result = run(db, args.gold)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'production_parity': result['production_parity'], 'selection': result['selection']}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
