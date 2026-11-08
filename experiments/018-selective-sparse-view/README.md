# Experiment 018 — selective sparse-view H3

Coordination: issue #188.

## Why this exists

Frozen H1 as a universal query replacement failed its independent holdout.
Frozen H2 equal-RRF fusion increased candidate recall but harmed Top-10 ranking.

A post-hoc analysis of the already-labeled 30 cases found one small,
mechanism-aligned signal: queries where at least half of unique analyzed tokens
were classified by frozen H1 as retrieval/conversational glue had 3 improved,
2 unchanged, and 0 worse H1 outcomes.

That observation is not independent evidence. It only motivates H3.

## Frozen H3 rule

The selector uses only query-shape information available before retrieval.

1. Analyze the natural query with the existing lemma analyzer and frozen H1
   token classifier.
2. Deduplicate by normalized token, preserving the first classification.
3. Compute:

       glue_ratio = unique_glue_tokens / unique_analyzed_tokens

4. If glue_ratio >= 0.5, use the frozen H1 sparse query.
5. Otherwise use the unchanged full lemma query.
6. Empty analyzed queries remain on the full-query path.

No retrieval scores, result-list statistics, target information, QPP model,
second retrieval, fusion, new index, or new model participates in the decision.

The 0.5 threshold was discovered post hoc from the previous 30 labeled cases.
Those cases are development evidence only and cannot validate H3.

H3 must be frozen unchanged and evaluated on a new independent historical-query
holdout before any serving decision.

## Development result

The fixed 0.5 selector was replayed over the three already-labeled 10-case sets
that motivated it. These are development data only.

| Set | Baseline reachable | H3 reachable | Baseline Top-10 | H3 Top-10 | Sparse routes | Improved / Same / Worse |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| original H1 development | 2 | 3 | 2 | 2 | 2 | 1 / 9 / 0 |
| H1 independent (now development for H3) | 5 | 5 | 5 | 5 | 2 | 1 / 9 / 0 |
| H2 independent (now development for H3) | 4 | 4 | 4 | 4 | 1 | 1 / 9 / 0 |

Across all 30 cases:
- 5 queries routed to sparse;
- 25 stayed on full lemma;
- 3 improved / 27 same / 0 worse;
- candidate reachability moved 11 -> 12;
- Top-10 remained 11 -> 11.

Because the threshold was discovered post hoc from these same cases, this result
cannot validate H3. The selector is now frozen unchanged. A new independent
historical-query holdout is required before any serving decision.

Public aggregate evidence:
receipts/188-selective-sparse-h3.public.json.
