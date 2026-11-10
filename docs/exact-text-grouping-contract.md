# Exact-text grouped result contract

Status: research contract under issue #188. It is implemented as a pure helper and
tests, but is **not wired into the Session Search HTTP/MCP serving path**.

## Problem

Current search output is event-shaped: one hit contains one session/message occurrence.
Real-corpus benchmark #188 showed that exact repeated bodies can consume Top-K slots.
The optimization must not turn repeated text into one historical event: accepted
session/message occurrences and branch provenance remain distinct authority.

## v1 group shape

Given an already-ranked finite candidate list, exact Unicode string equality forms
groups in first-ranked-occurrence order:

```json
{
  "text": "one body, stored once in the response group",
  "representative": {
    "session_id": "best-ranked occurrence",
    "message_id": "...",
    "ordinal": 12,
    "score": 0.42
  },
  "occurrence_count": 2,
  "occurrences": [
    {
      "session_id": "...",
      "message_id": "...",
      "ordinal": 12,
      "search_class": "dialogue",
      "provider": "chatgpt",
      "shard_id": "chatgpt-0001",
      "generation_id": "...",
      "shard_rank": 1
    }
  ]
}
```

`representative` is the first/best-ranked candidate occurrence with `text` removed;
it is presentation/ranking context, not a replacement identity. `occurrences` contains
provenance-bearing candidate events and does not repeat the body.

## Invariants

1. Group key is **exact text only**. No message-ID-only, normalized-text, hash-only,
   fuzzy, embedding or semantic collapse is allowed in this contract.
2. Same message ID with different bodies remains distinct.
3. Group order is the order of each body's first occurrence in the ranked candidate list.
4. v1 does not truncate occurrences: `occurrence_count == occurrences.length`.
5. Every input candidate occurrence appears in exactly one output group occurrence.
6. Provider, shard and generation provenance may differ across occurrences of one body.
7. Search `FOUND` / `NO_SIGNAL` / `PARTIAL`, shard availability and coverage semantics
   remain independent of grouping.
8. Accepted artifacts/ledger remain authority. A group is a retrieval/presentation
   object, not a new historical identity.

These are the response-level counterpart of the Lean pilot's event/blob distinction:
physical/retrieval text may be factored while events stay recoverable.

## Placement boundary

Grouping **after the final Top-K is too late** to recover slots already consumed by
duplicates. The accepted real-corpus benchmark kept the current K=10 recall candidate
pool (30 rows), applied the current rerank, grouped exact bodies, and only then selected
Top-10. Baseline prefix parity was 20/20.

Therefore a future serving implementation must group before the relevant per-shard
Top-K boundary (and may need a second merge of equal groups across shards). Merely
grouping `router.hits` after shards have already returned their final `limit` does not
implement the measured algorithm.

## Not claimed

- No semantic/near-duplicate equivalence.
- No MMR or embedding reranking.
- No change to BM25/FTS scoring.
- No proof that every historical occurrence of the same text is in a query's bounded
  candidate pool; the contract preserves all **candidate occurrences** presented to it.
- No production/API cutover under this document.

Implementation helpers: `functions/grouping.mjs` owns both single-list exact grouping and the pure, still-unwired `mergeExactTextGroups()` cross-shard group merge.
Real-corpus evidence: `receipts/188-exact-grouping-real-corpus.public.json`.

## Local SQLite reference implementation

`session_search.search.search_corpus_grouped_recall()` is an opt-in local
reference implementation. It reuses the same internal recall candidate query and
reranker as `search_corpus(..., recall=True)`, then applies exact-text grouping
before the final group Top-K. Its internal `candidate_budget` is explicit and
independent from the final `limit`; omitting it preserves the previous `3 * limit`
policy. Existing flat `search_corpus()` behavior remains the default and is unchanged.

Acceptance on the private frozen 20-row gold set:
- flat ranks from the refactored core matched the previously accepted #188 public
  receipt exactly;
- grouped ranks from `search_corpus_grouped_recall()` matched the accepted #188
  grouped ranks exactly for 20/20 cases;
- no CLI, HTTP, MCP, shard SQL or serving route uses grouped results by default.

This function is a reference seam for algorithm/conformance work, not a production
backend decision.

## Serverless/Postgres opt-in adapter

`sql/neon/009_exact_text_grouped_retrieval.sql` defines the experimental
`session_search.search_generation_grouped_v1()` seam. It is deliberately:

- pinned-generation only;
- recall-only;
- bounded to `min(300, result_limit * 3)` ranked event candidates;
- exact-text grouped before final group Top-K;
- provenance-preserving within the bounded candidate pool;
- separate from `search_generation_v2` / `search_v2`.

`functions/shard/core.mjs` exposes `buildGroupedSearchCall()` for conformance and
future guarded integration, but the current router/shard serving path still calls
the existing v2 functions.

A real PostgreSQL smoke was executed transactionally on an isolated benchmark
branch under the existing `session_search_owner` capability. For K=10 it observed
30 candidates -> 10 groups containing 12 selected event occurrences. The function
was dropped in the same transaction and an independent readback confirmed it was
absent afterward. Public receipt:
`receipts/188-serverless-grouped-v1-smoke.public.json`.

This proves SQL/runtime viability of the opt-in seam, not production relevance
parity on the federated serverless shards. Cutover remains a separate gated change.

`sql/neon/011_exact_text_grouped_candidate_budget.sql` adds the opt-in
`search_generation_grouped_v2()` canary contract. It keeps v1 intact but makes
`candidate_budget` a separate bounded argument from `result_limit`. The internal
JavaScript builder always supplies both values; when the canary caller omits an
explicit budget it preserves the measured parity policy
`min(300, 3 * result_limit)`. The authenticated shard `/search` handler can select
this path only with the internal `grouping: exact_text_v1` control; a candidate
budget without that selector fails closed. The public router validation rejects
`grouping` and `candidate_budget`, and the MCP schema exposes neither, so public
router/MCP behaviour remains flat and unchanged.

## Strong-ranker cross-check

A second real-data cross-check reused the surviving #118 two-stage lexical stack:
bounded fallback candidate generation plus `bm25_full` ranking. The historical
baseline ranks reproduced 20/20 exactly. With K=10 and the same bounded 30-row
post-rank pool used by the exact-grouping experiment:

- mean unique Top-10 bodies moved only from 9.7 to 10.0;
- R@1/R@3/R@5/R@10 were unchanged;
- MRR@30 was unchanged at 0.5425;
- 0/20 target ranks improved, 20/20 stayed equal, 0 worsened.

This materially changes the promotion interpretation. Exact grouping remains a
valid deterministic presentation/selection primitive, but the stronger BM25
ranker already suppresses most duplicate-slot pressure. Do not cut over grouping
as an isolated relevance optimization merely because it helped the weaker
current local recall stack. Re-evaluate it only as part of a measured serving
candidate/ranker pipeline or for a separately justified diversity/payload goal.

Public receipt:
`receipts/188-bm25-exact-grouping-crosscheck.public.json`.
