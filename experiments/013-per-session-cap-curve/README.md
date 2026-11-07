# Per-session cap survival curve (#206)

## Why this experiment exists

The recall path historically acquired a conservative per-session cap of 3 to stop
one session from monopolizing the candidate/result window. Later real-data work
showed that this guardrail can also discard useful evidence before reranking.

A hand-picked comparison such as 3/5/10 would risk treating convenient integers
as if they were meaningful constants. Search systems instead treat candidate and
rerank depths as tunable resource/recall budgets:

- Elasticsearch separates final `k` from `num_candidates`; increasing candidate
  depth tends to improve recall at additional cost:
  https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/knn-retriever
- Vespa exposes configurable rerank budgets because phased-ranking quality/cost
  trade-offs depend on the workload:
  https://docs.vespa.ai/en/ranking/phased-ranking.html

## Fixed experiment

- final K = 10;
- global candidate budget = 30;
- query tokenization unchanged;
- `rerank_recall_rows()` unchanged;
- frozen development gold unchanged;
- independent #203 holdout remains validation-only.

For a 30-row global candidate pool, caps above 30 cannot affect the result.
Therefore the complete meaningful domain is every integer cap from 1 through 30.

The benchmark retrieves one maximal raw evidence set per query, then applies every
cap to that same evidence in memory before invoking the unchanged reranker. This
makes points directly comparable and avoids 30 ad-hoc SQL variants.

## Selection rule

The rule was recorded in #206 before the full curve was read:

1. exclude any cap that loses a Top-10 hit relative to current cap=3;
2. maximize aggregate R@10;
3. if tied, maximize candidate recall in the 30-row pool;
4. if still tied, choose the smallest cap;
5. report session concentration/cost diagnostics; do not silently override the
   rule if concentration becomes pathological.

The independent holdout may validate or reject the selected cap, but may not
retune it.

## Result

Production-shaped cap=3 reproduced exactly on 20/20 frozen queries.

Key points:

- cap=3: candidate recall 0.55, R@10 0.35;
- cap=5: candidate recall 0.60, R@10 0.40;
- cap=7: candidate recall 0.65, R@10 0.40;
- caps 8..30 add no further candidate-recall or R@10 gain.

Therefore cap=7 is selected by the predeclared rule: it is the smallest cap that
combines the maximum observed R@10 with the maximum observed candidate recall
while losing no existing Top-10 hit.

The curve also exposes the cost of relaxing the guardrail. Mean maximum
single-session share in the bounded pool rises from about 0.092 at cap=3 to 0.145
at cap=7, while mean Top-10 unique sessions moves from 10.0 to 9.6. This is a
small measurable diversity cost, not a hidden free lunch.

A direct one-pass latency smoke over the same 20 queries found no material timing
jump:

- cap=3: mean ~785 ms, p50 ~630 ms, p95 ~1424 ms;
- cap=7: mean ~786 ms, p50 ~651 ms, p95 ~1429 ms.

These are comparative diagnostic numbers, not production SLO measurements.

## Decision

`cap=7,budget=30` supersedes the earlier provisional `cap=5` hypothesis as the
preselected challenger for independent holdout #203.

No serving change is justified here. #203 must compare current `(3,30)` against
this already-selected `(7,30)` without reopening the cap grid.

Public evidence:
`receipts/206-per-session-cap-curve.public.json`.
