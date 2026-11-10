# Exact-text grouped result contract

Status: accepted default result contract under #264, based on #188/#258. Exact-text grouping is the normal local, shard, router, and MCP result shape. The old flat event-row shape remains only as an explicit internal rollback/replay seam.

## Problem

Historical search output was event-shaped: one hit contained one session/message occurrence.
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

The default serving implementation therefore groups before the relevant per-shard
Top-K boundary and merges equal groups again across shards before the global Top-K.
Grouping only after a flat final Top-K would still be too late and is not the default
algorithm.

## Not claimed

- No semantic/near-duplicate equivalence.
- No MMR or embedding reranking.
- No change to BM25/FTS scoring.
- No proof that every historical occurrence of the same text is in a query's bounded
  candidate pool; the contract preserves all **candidate occurrences** presented to it.
- Grouping becoming the default result shape is an architectural/presentation decision under #264; it is not a claim that grouping alone improves relevance for every ranker.

Implementation helpers: `functions/grouping.mjs` owns both single-list exact grouping and `mergeExactTextGroups()` for the federated cross-shard merge.
Real-corpus evidence: `receipts/188-exact-grouping-real-corpus.public.json`.

## Local SQLite reference implementation

`session_search.search.search_corpus()` now returns exact-text groups by default for both strict and recall modes. `search_corpus_grouped_recall()` remains a compatibility name for the grouped recall path. It reuses the same internal recall candidate query and
reranker as `search_corpus(..., recall=True)`, then applies exact-text grouping
before the final group Top-K. Its internal `candidate_budget` is explicit and
independent from the final `limit`; omitting it preserves the previous `3 * limit`
policy. The former event-row behavior is available only through the explicit internal `search_corpus_flat()` rollback/replay helper.

Historical #188 local acceptance on the private frozen 20-row gold set:
- flat ranks from the refactored core matched the previously accepted #188 public
  receipt exactly;
- grouped ranks from `search_corpus_grouped_recall()` matched the accepted #188
  grouped ranks exactly for 20/20 cases.

At that checkpoint grouping was still a reference seam and was not the serving
default. #264 later superseded that deployment state and promoted the same contract
to the normal local/shard/router/MCP result shape.

## Historical Postgres precursors and current grouped adapter

`sql/neon/009_exact_text_grouped_retrieval.sql` defines the experimental
`session_search.search_generation_grouped_v1()` seam. It is deliberately:

- pinned-generation only;
- recall-only;
- bounded to `min(300, result_limit * 3)` ranked event candidates;
- exact-text grouped before final group Top-K;
- provenance-preserving within the bounded candidate pool;
- separate from `search_generation_v2` / `search_v2`.

`functions/shard/core.mjs` originally exposed `buildGroupedSearchCall()` only for
conformance and guarded integration. That statement describes the #188 precursor,
not the current serving state: after #264 the normal router explicitly requests the
grouped v3 path, while the older flat v2 path is retained only for rollback/replay
and rolling-upgrade compatibility.

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
the grouped SQL path with an explicit candidate budget. Under #264, the normal shard/router path selects grouped results by default. `grouping`, `candidate_budget`, and `result_shape` remain internal controls and are not exposed in the public MCP input schema.

The historical authenticated canary route remains disabled by default for diagnostic compatibility. The normal `/search` route and MCP `session_search` tool now use the same grouped merge directly. Production source defaults to `grouped_v1`; `SESSION_SEARCH_RESULT_SHAPE=flat_v1` is an internal rollback switch, not a public search option.

`sql/neon/012_exact_text_grouped_default.sql` is the current grouped serving migration. It adds `search_generation_grouped_v3()` plus the ACTIVE-generation `search_grouped_v3()` wrapper, supports both strict and recall retrieval, and keeps `candidate_budget` separate from the final result limit. The current router sends `result_shape=grouped_v1` to shards; a missing internal result-shape field remains flat only so an older router can coexist during a rolling upgrade.

Verified production checkpoint on 2026-11-10: router deployment #10 was active/completed; a live App-backed Session Search probe returned `FOUND` with `groups[]`, no legacy `hits[]`, four queried shards and zero unavailable shards. This is runtime evidence for that checkpoint, not a permanent topology constant.

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
ranker already suppresses most duplicate-slot pressure. This cross-check still limits the claim: grouping is not a universal relevance improvement. #264 promotes it because duplicate bodies should not consume separate user-facing result slots while occurrence provenance is preserved, not because the stronger ranker showed a relevance gain.

Public receipt:
`receipts/188-bm25-exact-grouping-crosscheck.public.json`.
