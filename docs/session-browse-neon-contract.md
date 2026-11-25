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

## Native internal reader — laboratory candidate (#345)

The existing router handler optionally accepts a separate
`sessionBrowseAuthToken` constructor parameter for POST /session-browse.
The default is null (404); no environment binding is introduced, so the
existing deployed entry point cannot enable this route. The synthetic lab
passes a distinct token and reuses the exact makeSessionBrowser function.
The route checks auth before parsing or dispatch, rejects token reuse with
the existing router/shard token, and the dedicated token does not authorize
search, coverage, MCP (including feedback), or the grouped canary.

Successful and BLOCKED bodies use the existing browse contract. HTTP 200
alone does not establish success; invalid input is 400. This is a
route-scoped read credential, NOT authorization for a particular session
or source scope. Do not deploy, expose private history, provision real
credentials, or enable the route before the source-access and durable
refresh/serving handoff design is accepted. No new service, schema, DB role,
cursor format or reader implementation. Tests use synthetic shard results;
live latency, costs, full provenance and cloud authorization are unverified.
See theseus-research PR #140 for the comparative architecture.
