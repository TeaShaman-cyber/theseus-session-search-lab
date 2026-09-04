# Capture adapter contract

A capture adapter turns a session-history source into an artifact the importer can validate and normalize.

## Required properties

1. Stable artifact boundary.
2. Explicit format/schema identifier when available.
3. Integrity metadata sufficient to detect corruption or partial writes.
4. Session/message ordering or timestamps.
5. Coverage metadata when the source can be partial or paginated.
6. No claim that a missing message means the event never happened unless coverage is independently complete.

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

This membership requirement is **not yet enforced by the ordinary Barn Doctor importer**. Legacy Barn Doctor normalization can derive `COMPLETE_EXPOSED_CONVERSATION` from pagination metadata even when one or more included message pages lack independently proven conversation membership. Until provenance-aware recovery in Issue #16 is implemented and verified, such a Barn Doctor `COMPLETE_EXPOSED_CONVERSATION` value records an observed pagination boundary only and **must not be used as evidence of absence** for historical claims.

If a portable artifact exposes multiple stable conversation identities, the ordinary importer remains fail-closed with `BLOCKED_MIXED_SESSION_ARTIFACT`. Provenance-aware preprocessing is a separate adapter/recovery layer; see [Mixed capture artifact recovery](mixed-artifact-recovery.md) and Issue #16.
