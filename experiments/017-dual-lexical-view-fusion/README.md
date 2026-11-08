# Experiment 017 — dual lexical view RRF H2

Coordination: issue #188.

## Question

Can two complementary lexical query views preserve the strengths of the natural
lemma query while retaining sparse-view rescues, without changing the backend,
index, model, per-session cap or global candidate budget?

This H2 was motivated only after frozen H1 failed as a standalone replacement on
an independent holdout but showed complementary failure modes.

## Frozen H2

Views:

1. full natural-query lemma view;
2. frozen H1 sparse-view planner from experiment 016.

Each view uses the existing FTS5/BM25 seam with:

- per-session cap = 3;
- candidate budget = 30.

Fusion is standard unweighted Reciprocal Rank Fusion:

    score(candidate) = sum_view 1 / (60 + rank_view)

Rules:

- k = 60;
- candidate identity is the existing row identity
  (session_id, ordinal, text_fp);
- no new content deduplication;
- union both top-30 lists;
- deterministic tie-break:
  higher RRF score, lower best individual rank, lower full-view rank, lower
  sparse-view rank, session_id, ordinal;
- truncate fused output back to 30 candidates.

The existing 10-case H1 independent holdout is development evidence only for H2,
because its transition pattern motivated this hypothesis.

No parameter may be changed after inspecting H2 target ranks on that set.

If H2 shows a development signal, freeze it unchanged and evaluate on a new
independent historical-query holdout before any serving decision.

No backend, storage, model, index authority, deployment or cutover changes are
in scope.

## Development result

On the 10-case development set that exposed H1 complementarity:

| Arm | reachable / 10 | Top-10 / 10 |
| --- | ---: | ---: |
| full lemma baseline | 5 | 5 |
| frozen H1 sparse view | 4 | 4 |
| frozen H2 RRF fusion | 6 | 5 |

H2 vs baseline produced 3 improved / 5 same / 2 worse cases.

Rank-class transitions were:
- 4 baseline Top-10 targets remained Top-10;
- 1 baseline Top-10 target moved to ranks 11-30;
- 1 baseline-unreachable target was rescued into Top-10;
- 4 remained unreachable.

This is a development signal only. H2 is now frozen unchanged. The next gate is
a new independent historical-query holdout. Do not tune k, view weights, budgets,
tie-breaks, H1, or candidate identity on this development set.

Public aggregate evidence is in
receipts/188-dual-lexical-rrf-h2.public.json.
