# Capture adapter contract

A capture adapter turns a session-history source into an artifact the importer can validate and normalize.

## Required properties

1. Stable artifact boundary.
2. Explicit format/schema identifier when available.
3. Integrity metadata sufficient to detect corruption or partial writes.
4. Session/message ordering or timestamps.
5. Coverage metadata when the source can be partial or paginated.
6. No claim that a missing message means the event never happened unless coverage is independently complete.


## Portable authority matrix

The portable boundary is provider-neutral and representation-agnostic. A ZIP, JSON document, SQLite database, Markdown file, or transport channel does not become authoritative merely because of its format.

| Layer | Responsibility | Authority boundary |
| --- | --- | --- |
| **Source capture bytes + content hash** | Evidence of the exact bytes presented to an adapter/importer | Authoritative for that captured artifact only; not proof of complete platform history |
| **Versioned schema + adapter interpretation** | Defines how provider/source bytes map to stable session identity, message identity, ordering, coverage, roles, and provenance | Authoritative only for the normalization semantics encoded by that schema/adapter version |
| **Capture manifest / transformation receipt** | Integrity, source provenance, coverage observations, recovery/member selection | Authoritative only for claims explicitly verified by that capture/recovery operation |
| **Accepted-artifact ledger** | Durable corpus membership | Authoritative for which immutable artifacts belong to the corpus |
| **Normalized corpus state** | Regeneratable accepted state derived from ledger members + immutable artifacts | Derived state; never historical source authority by itself |
| **Retrieval projection** | FTS/ranking/search convenience | Disposable projection; never authority for whether an event happened |
| **Transport/storage channel** | Byte delivery | Carrier only; successful delivery does not imply parse, acceptance, persistence, or refresh |

The stable non-equivalences are:

```text
capture_present != complete_history
transport_success != artifact_accepted
artifact_accepted != projection_verified
search_miss != historical_absence
message_id_overlap != session_identity
```

A portable artifact therefore needs enough evidence for the adapter to establish integrity, versioned interpretation, stable session identity, ordering, coverage, and source provenance. Acceptance into a corpus is a separate operation with its own durable ledger/receipt. Deterministic rebuild and search projections consume accepted artifacts; they do not upgrade the historical authority of those artifacts.

## Initial Barn Doctor adapter observation

The first development prototype observed `barn-doctor-export:v1` from Barn Doctor `0.2.3`. Its manifest can contain member byte counts and SHA-256 digests, and its optional conversation payload can be direct UTF-8 JSON.

This is an observed adapter format, not the canonical Session Search schema.
## Paginated capture requirements

For a multi-page artifact, the adapter/importer boundary preserves:

- manifest order as capture sequence;
- page start/end cursors when present;
- `has_previous_page` / `has_next_page`;
- message count and time bounds for each payload page;
- message-to-page provenance.

The importer may deduplicate repeated `message_id` values only when the complete message objects are identical. A conflicting duplicate is a hard import failure.

Coverage is explicit:

- `PARTIAL_SESSION_SLICE` when the oldest observed page still reports earlier history;
- `COMPLETE_EXPOSED_CONVERSATION` when the oldest observed page reports `has_previous_page=false`.

`COMPLETE_EXPOSED_CONVERSATION` is scoped to the history exposed by the captured source for that conversation. It does not imply completeness across other chats, deleted history, inaccessible branches, or unobserved provider state.

## Cross-conversation contamination and membership provenance

A capture artifact can contain provider fetches for more than one conversation even when the user-visible workflow appears to be focused on one active chat. Therefore payload membership is an explicit capture concern, not something the importer may infer from proximity.

For adapters that record network request metadata, preserve enough provenance to bind each captured payload to its request and stable provider conversation identity when available. For Barn Doctor-style captures this includes the request key, endpoint class, and `conversationId` associated with captured `conversation_get` / `conversation_messages` bodies.

A complete pagination claim requires both:

1. coverage evidence showing the exposed history boundary was reached; and
2. membership evidence showing every included page belongs to the claimed conversation.

The ordinary Barn Doctor importer still does not retroactively prove membership for legacy pages that lack independently captured request/session provenance. Provenance-aware Barn recovery is implemented by the separate recovery path from Issue #16 and can materialize a derived single-session artifact when request provenance proves ownership. That recovery capability does not strengthen already accepted legacy evidence by itself. A legacy Barn Doctor `COMPLETE_EXPOSED_CONVERSATION` value without proven page membership records an observed pagination boundary only and **must not be used as evidence of absence** for historical claims.

If a portable artifact exposes multiple stable conversation identities, the ordinary importer remains fail-closed with `BLOCKED_MIXED_SESSION_ARTIFACT`. Provenance-aware preprocessing is a separate adapter/recovery layer; see [Mixed capture artifact recovery](mixed-artifact-recovery.md) and Issue #16.
## Official ChatGPT account-export adapter

The ChatGPT account-export adapter treats the provider mapping graph as authoritative historical evidence. `parent` links define graph structure; `current_node` is an observed active terminal marker, not branch identity authority. Every terminal leaf is materialized as its own root-to-leaf portable artifact so alternate continuations are never concatenated into fictitious dialogue.

The base semantic session follows the uniquely earliest timestamped child at every fork. Any branch that deviates from that base receives a deterministic path-choice suffix. If a fork lacks enough distinct timestamp evidence to choose the base branch deterministically, ingestion blocks rather than relying on JSON member or mapping order. Missing message timestamps on non-forking paths remain unknown.

Account export ZIPs are snapshots, not proof of complete provider history, so ChatGPT-derived children are conservatively marked `PARTIAL_SESSION_SLICE`. Each child records the adapter version, parent ZIP digest, source conversation identity, source conversations member, selected leaf, observed `current_node`, branch mode, and whether the selected leaf was current in that snapshot. A branched snapshot family is accepted for branch reconciliation only when its declared branch set is complete; a lone child from a multi-branch snapshot does not become branch authority.

When a legacy/raw artifact shares the provider conversation identity of an accepted branched ChatGPT family, source identity and projected branch identity are deliberately separated. Exact `(message_id, canonical_message_sha256)` evidence is intersected across official branches. Evidence common to every branch is non-discriminating; provider-only raw rows absent from all official branches are ignored for routing. A unique branch match becomes derived `BRANCH_MATCH`; contradictory branch-only evidence fails closed; common-only or zero-overlap evidence becomes deterministic `UNRESOLVED` isolation. Multimodal text projections may additionally contribute the cryptographically verified raw-content lineage digest. Accepted artifact bytes and ledger metadata are never rewritten by this routing step.

This routing contract is projection schema `session-search-corpus-v2`. A v1 projection must be rebuilt from accepted evidence before ordinary writes continue. Rebuild/verification derive and compare the routing table alongside sessions, messages, provenance, and FTS; manual SQLite or ledger surgery is not a repair mechanism.

Multimodal user/assistant records are split into a searchable text projection plus an explicit trace retaining the provider multimodal structure and message metadata. Hidden reasoning content remains non-dialogue according to the canonical normalizer. Real account data is private acceptance evidence only; public fixtures must be synthetic.
