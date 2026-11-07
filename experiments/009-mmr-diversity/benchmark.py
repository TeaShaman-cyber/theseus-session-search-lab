#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3

from session_search.corpus_store import CorpusPaths
from session_search.search import _recall_candidate_rows, group_exact_search_rows, query_tokens

SCOPES = ['dialogue', 'evidence']
LAMBDA_GRID = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]


def content_fp(text: str) -> str:
    normalized = re.sub(r'\s+', ' ', text.strip().lower())
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()


def token_jaccard(left: str, right: str) -> float:
    a = set(query_tokens(left))
    b = set(query_tokens(right))
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def mmr_select(groups: list[dict], *, k: int, lambda_relevance: float) -> list[dict]:
    if k < 1:
        raise ValueError('MMR_K_INVALID')
    if not isinstance(lambda_relevance, (int, float)) or not 0.0 <= lambda_relevance <= 1.0:
        raise ValueError('MMR_LAMBDA_INVALID')
    if not groups:
        return []

    count = len(groups)
    relevance = [1.0 if count == 1 else 1.0 - index / (count - 1) for index in range(count)]
    selected: list[int] = []
    remaining = list(range(count))

    while remaining and len(selected) < k:
        def key(index: int):
            redundancy = max(
                (token_jaccard(groups[index]['text'], groups[prior]['text']) for prior in selected),
                default=0.0,
            )
            score = lambda_relevance * relevance[index] - (1.0 - lambda_relevance) * redundancy
            return score, -index

        best = max(remaining, key=key)
        selected.append(best)
        remaining.remove(best)

    return [groups[index] for index in selected]


def target_rank(groups: list[dict], target_fp: str) -> int | None:
    for index, group in enumerate(groups, 1):
        if content_fp(group['text']) == target_fp:
            return index
    return None


def retrieval_metrics(ranks: list[int | None]) -> dict:
    count = len(ranks)
    return {
        'count': count,
        'r_at_1': sum(r is not None and r <= 1 for r in ranks) / count,
        'r_at_3': sum(r is not None and r <= 3 for r in ranks) / count,
        'r_at_5': sum(r is not None and r <= 5 for r in ranks) / count,
        'r_at_10': sum(r is not None and r <= 10 for r in ranks) / count,
        'mrr': sum((1 / r) for r in ranks if r is not None) / count,
    }


def _improved(before: int | None, after: int | None) -> bool:
    return (before is None and after is not None) or (
        before is not None and after is not None and after < before
    )


def _worse(before: int | None, after: int | None) -> bool:
    return (after is None and before is not None) or (
        before is not None and after is not None and after > before
    )


def run(corpus_root: pathlib.Path, gold_file: pathlib.Path, *, k: int = 10) -> dict:
    if k < 1:
        raise ValueError('MMR_K_INVALID')
    gold = json.loads(gold_file.read_text(encoding='utf-8'))
    paths = CorpusPaths.from_root(corpus_root)
    conn = sqlite3.connect(f'file:{paths.db.resolve()}?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    cases = []
    try:
        for item in gold:
            candidates = _recall_candidate_rows(conn, item['query_text'], SCOPES, k, None)
            groups = group_exact_search_rows(candidates)
            baseline_rank = target_rank(groups[:k], item['target_content_fp'])
            lambda_ranks = {}
            for value in LAMBDA_GRID:
                selected = mmr_select(groups, k=k, lambda_relevance=value)
                lambda_ranks[str(value)] = target_rank(selected, item['target_content_fp'])
            cases.append({
                'case_id': item['case_id'],
                'query_lang': item['query_lang'],
                'baseline_rank': baseline_rank,
                'lambda_ranks': lambda_ranks,
                'candidate_group_count': len(groups),
            })
    finally:
        conn.close()

    baseline_ranks = [case['baseline_rank'] for case in cases]
    lambda_summary = {}
    for value in LAMBDA_GRID:
        key = str(value)
        ranks = [case['lambda_ranks'][key] for case in cases]
        lambda_summary[key] = {
            'metrics': retrieval_metrics(ranks),
            'improved': sum(_improved(before, after) for before, after in zip(baseline_ranks, ranks)),
            'same': sum(before == after for before, after in zip(baseline_ranks, ranks)),
            'worse': sum(_worse(before, after) for before, after in zip(baseline_ranks, ranks)),
        }

    return {
        'schema': 'theseus.session-search-mmr-diversity-benchmark.v1',
        'k': k,
        'candidate_budget': k * 3,
        'similarity': 'query_tokens_set_jaccard',
        'relevance': 'linear_normalized_rank_after_exact_grouping',
        'lambda_grid': LAMBDA_GRID,
        'baseline': retrieval_metrics(baseline_ranks),
        'lambdas': lambda_summary,
        'cases': cases,
        'selection_boundary': 'exploratory_same_gold_no_production_lambda_selection',
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--corpus', type=pathlib.Path, required=True)
    parser.add_argument('--gold', type=pathlib.Path, required=True)
    parser.add_argument('--k', type=int, default=10)
    parser.add_argument('--out', type=pathlib.Path)
    args = parser.parse_args()
    result = run(args.corpus, args.gold, k=args.k)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if args.out:
        args.out.write_text(payload, encoding='utf-8')
    print(payload, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
