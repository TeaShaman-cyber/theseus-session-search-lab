# Experiment 009 — bounded MMR diversity diagnostic (#128)

Question: after exact-text grouping from #188, does classical Maximal Marginal
Relevance (MMR) justify another retrieval component on the same frozen real-corpus
gold set?

## Fixed protocol

- recovered local SQLite corpus: same ss147 candidate used by #188;
- frozen #116/#127 gold: 20 RU/EN rows / 10 target fingerprints;
- current recall candidate budget for K=10: 30 event rows;
- current rerank unchanged;
- exact-text grouping applied first;
- MMR then selects 10 groups;
- relevance surrogate: linear normalized **existing rank**, not raw backend score;
- similarity: Jaccard over existing `query_tokens()` token sets;
- fixed lambda grid: 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95.

Using rank rather than backend score is deliberate: #128 candidate v1 records that
SQLite FTS5/BM25 and Postgres `ts_rank_cd` have opposite score orientation and do
not form one portable numeric scale.

## Result

Exact-grouped baseline:
- R@10 0.40;
- R@3 0.30;
- R@5 0.35;
- MRR 0.22798.

Observed grid:
- lambda >= 0.5: no Top-10 changes in any of 20 cases;
- lambda 0.4: 1 improved / 1 worse, aggregate essentially unchanged;
- lambda 0.3: 1 improved / 1 worse, R@5 0.35 -> 0.40;
- lambda 0.2: 2 improved / 1 worse, R@10 0.40 -> 0.45, R@3 0.30 -> 0.35,
  MRR 0.22798 -> 0.24667;
- lambda 0.1: 2 improved / 3 worse;
- lambda 0.0: 2 improved / 4 worse and clear aggregate regression.

**No lambda both improves a target and has zero regressions.**

Lambda 0.2 is therefore an exploratory same-gold signal only, not an accepted
parameter. Selecting it on the same benchmark used to discover it would be
parameter tuning on the evaluation set.

## Decision

MMR is **deferred**. Exact-text grouping already removes most strong redundancy,
and the remaining MMR trade-off is not clean enough to justify another production
ranking stage today.

A future MMR revisit needs at least one of:
- a separate holdout set;
- a broader labeled query corpus;
- a materially stronger residual near-duplicate signal;
- a justified similarity representation that is not tuned on this same gold.

No embeddings, vector store, provider call, production ranker change, or serving
cutover is introduced by this experiment.

Public receipt: `receipts/128-mmr-diversity-real-corpus.public.json`.

Disposition:
`MMR_CHARACTERIZED / NO_REGRESSION_FREE_GAIN / PARAMETER_SELECTION_DEFERRED / NO_CUTOVER`.
