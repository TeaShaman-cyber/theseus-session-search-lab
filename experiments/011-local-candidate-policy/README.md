# Local recall candidate-policy characterization (#201)

Question: before adding another ranker, is the current local recall loss mainly caused by the global candidate budget, the per-session candidate cap, or both?

The benchmark keeps query tokenization and `rerank_recall_rows()` unchanged. Only two pre-rerank bounds vary:

- `per_session_cap`;
- `candidate_budget`.

Private inputs are the recovered ss147 SQLite corpus and the frozen 20-row RU/EN gold set. The public receipt contains only case IDs, languages, ranks, aggregates, timing summaries, and policy parameters.

## Baseline control

The current production-shaped local policy for K=10 is `per_session_cap=3`, `candidate_budget=30`. The benchmark independently compares its ordered candidate list with `_recall_candidate_rows(..., limit=10)` for every frozen query. Accepted parity is 20/20.

## Result

Current policy:

- candidate recall: 11/20 = 0.55;
- R@10: 7/20 = 0.35;
- MRR: 0.207589.

Increasing only the global budget to 60/120/300 did not produce a clean gain. At 300 it reached one additional target but lost four existing Top-10 hits relative to current.

Increasing only the per-session cap while keeping budget=30 produced the cleanest signal:

- cap=5: candidate recall 0.60, R@10 0.40, MRR 0.211496; 4 ranks improved, 16 same, 0 worse; +1 reachable, +1 Top-10, 0 Top-10 losses;
- cap=10: candidate recall 0.65, R@10 0.40, MRR 0.216699; 5 improved, 15 same, 0 worse; +2 reachable, but still only +1 Top-10;
- cap=30: same candidate recall/R@10 class as cap=10 with no additional Top-10 gain.

Observed p50 latency for cap=5/budget=30 was effectively the same as current in this single-pass diagnostic. Treat latency numbers as comparative smoke evidence, not a production SLO claim.

## Decision boundary

`cap=5,budget=30` is the smallest measured candidate-policy lever with a relevance gain and no regressions on this frozen set. It is **not yet a cutover decision** because the same gold set was used to discover this parameter. Promoting cap=5 without an independent holdout would be parameter tuning on the evaluation set.

Next step: find or construct an independently frozen real-query holdout, then compare only current `(3,30)` against the already-selected challenger `(5,30)`. Do not reopen the full grid on that holdout.

Public evidence: `receipts/201-local-candidate-policy.public.json`.
