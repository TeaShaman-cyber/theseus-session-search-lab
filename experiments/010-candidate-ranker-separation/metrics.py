from __future__ import annotations


def summarize_cases(cases: list[dict], *, k: int) -> dict:
    if not cases:
        raise ValueError('CASES_EMPTY')
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:
        raise ValueError('K_INVALID')

    reachable = 0
    topk = 0
    reciprocal_sum = 0.0

    for case in cases:
        candidate_reachable = case.get('candidate_reachable')
        ranked_rank = case.get('ranked_rank')
        if not isinstance(candidate_reachable, bool):
            raise ValueError('CANDIDATE_REACHABILITY_INVALID')
        if ranked_rank is not None and (
            not isinstance(ranked_rank, int)
            or isinstance(ranked_rank, bool)
            or ranked_rank < 1
        ):
            raise ValueError('RANK_INVALID')
        if not candidate_reachable and ranked_rank is not None:
            raise ValueError('UNREACHABLE_HAS_RANK')

        if candidate_reachable:
            reachable += 1
        if ranked_rank is not None:
            reciprocal_sum += 1.0 / ranked_rank
            if ranked_rank <= k:
                topk += 1

    count = len(cases)
    return {
        'count': count,
        'candidate_reachable': reachable,
        'candidate_recall': reachable / count,
        'topk_hits': topk,
        'r_at_k': topk / count,
        'reachable_to_topk': topk / reachable if reachable else 0.0,
        'mrr': reciprocal_sum / count,
    }

def main() -> int:
    import argparse
    import json
    import pathlib

    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=pathlib.Path, required=True)
    parser.add_argument('--k', type=int, default=10)
    args = parser.parse_args()
    cases = json.loads(args.input.read_text(encoding='utf-8'))
    print(json.dumps(summarize_cases(cases, k=args.k), sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
