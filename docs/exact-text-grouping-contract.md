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

Implementation helper: `functions/grouping.mjs`.
Real-corpus evidence: `receipts/188-exact-grouping-real-corpus.public.json`.
