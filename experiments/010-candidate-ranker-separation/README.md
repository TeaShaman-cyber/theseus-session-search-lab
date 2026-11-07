# Candidate recall vs ranker quality (#128)

This experiment keeps two failure modes separate:

1. candidate generation did not retain the target in the bounded candidate set;
2. the target was reachable, but the ranker placed it below the requested Top-K.

The metric core consumes only public-shaped case records:

```json
[
  {"candidate_reachable": true, "ranked_rank": 3},
  {"candidate_reachable": false, "ranked_rank": null}
]
```

For a fixed K it reports:

- candidate recall = reachable / all cases;
- R@K = target rank <= K / all cases;
- reachable-to-TopK = Top-K hits / reachable cases;
- MRR over all cases, with missing ranks contributing zero.

This metric layer is backend-neutral. It does not inspect raw backend score values
and therefore does not mix SQLite `lower_better` scores with Postgres
`higher_better` scores. Candidate v1 remains the provenance/score contract.

## Real-source demonstration — 2026-11-07

Frozen authority: the 20-row / 10-paired-case RU/EN gold set used by #118/#127.

### Local current recall

Existing local K=10 policy uses a bounded 30-row candidate pool. On the recovered
ss147 SQLite corpus:

- candidate reachable: 11/20 = 0.55;
- Top-10 hits: 7/20 = 0.35;
- reachable-to-Top10: 7/11 = 0.636;
- MRR over the bounded pool: 0.207589.

This means candidate loss is already material before any more sophisticated
reranker can help.

### Serverless #118 candidate gate

The surviving #118 fallback candidate gate retained 20/20 targets. Candidate counts
were min 55, p50 335, p95 544.4, max 609.

On that same reachable candidate set:

- `ts_rank_cd`: R@10 0.25, MRR 0.068370;
- `bm25_full`: R@10 0.75, MRR 0.544835.

Here candidate recall is not the measured bottleneck; ranker choice is.

### Exact grouping boundary

The #188 BM25 cross-check then applied exact-text grouping after ranking and found
no target-rank movement (0 improved / 20 same / 0 worse). This prevents grouping
or MMR from being mistaken for a fix to candidate/ranker failures.

Public evidence: `receipts/128-candidate-ranker-separation.public.json`.
