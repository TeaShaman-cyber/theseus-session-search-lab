# Session browse: pinned Neon/ChatGPT App prototype (#267)

This is the read-only cloud counterpart of local SQLite session_read in
PR #268. The database projection is derived; accepted artifacts/ledger remain
authoritative. This branch is not a deployed host or Arcade acceptance.

## Reader route

1. A session_search hit identifies an occurrence, including session_id
   (branch-scoped where applicable), shard_id, and generation_id.
2. The separate MCP session_browse tool accepts those exact identifiers,
   optional start_ordinal, limit, max_chars, or subsequent cursor.
   No lexical query, text deduplication, authorization token in cursor,
   additional storage service, or cross-shard merge.
3. Router checks one catalog entry, READY state, exact generation and
   provenance before querying the shard /session-read endpoint. Failures are
   BLOCKED, not an empty complete history. It never falls back to ACTIVE.
4. Shard calls versioned reader-only Neon function
   session_search.read_generation_session_v1, which checks READY generation
   and session existence, walks ordinal plus message_id COLLATE C, and reads
   bounded text segments. Postgres TEXT cannot represent NUL bytes;
   unlike SQLite, text offsets here count Unicode characters, not UTF-8 bytes.
5. Cursor version 1 contains session, shard, generation and
   (ordinal,message_id,character_offset); no checksum or authentication
   scheme. Caller can repeat the same read-only cursor after a failed request.
   Router checks ordering and bounds in each returned page.
   has_more=false means the accepted projection is exhausted, not that the
   entire original platform history was recovered. coverage_complete is true
   only when this is the final page AND the stored coverage state is exactly
   COMPLETE_EXPOSED_CONVERSATION. Partial source slices remain explicit.

## Verified scope and rollout boundary

- Deterministic local router/shard MCP tests cover exact pinning, UTF-8,
  duplicate text, identical ordinals with distinct IDs, empty messages,
  failures on later pages, generation changes and cursor errors.
- SQL migration 013_pinned_session_browse.sql is additive. Existing
  session_search_reader role gets EXECUTE only, no direct table SELECT.
- Isolated PostgreSQL 16 smoke (Docker, no network): schema/role/lifecycle
  migrations plus 013 applied; reader role denied direct table SELECT; real
  SQL pagination reconstructs synthetic data across 1/2/6-message budgets and
  rejects invalid positions or absent generations.
- No live Neon database was mutated. No shard/router runtime was deployed or
  ChatGPT App/Arcade hosted tool registration changed.
- Hosted rollout requires separate approval/acceptance: migrate target
  Neon DB, deploy matching shard, deploy router with tool exposure, verify
  hosted App discovery and real read-only pagination on pinned generation,
  check partial/unavailable behavior, and maintain rollback.

The existing lexical session_search path and its exact-text grouping remain
unchanged.
