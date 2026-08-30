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
