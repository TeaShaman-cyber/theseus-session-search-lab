# Independent Session Search serving plane

Issue #64 moves the Session Search read path out of MarcoPolo.

## Request path

```text
ChatGPT
  -> Arcade Session Search gateway
  -> Neon router Function
      -> Neon shard Function: chatgpt-0001
          -> bounded session_search.search(...)
      -> Neon shard Function: hermes-0001
          -> bounded session_search.search(...)
```

MarcoPolo remains an engineering, ingest, QA, rebuild, and recovery workbench. It is not part of the deployed HTTP/MCP request path.

## Function split

The shard Function validates one bounded search request and executes exactly one versioned Postgres function call. It does not accept arbitrary SQL.

The router Function has no shard database credentials. Its catalog contains shard identity, provider, expected generation, state, and HTTPS endpoint. It selects relevant READY shards, performs HTTP fan-out, and merges results deterministically.

The same federated search implementation serves both:
- `POST /search`;
- the MCP `session_search` tool at `POST /mcp`.

## Failure semantics

- `FOUND`: every selected shard responded and at least one hit exists.
- `NO_SIGNAL`: every selected shard responded and no hit exists.
- `PARTIAL`: one or more selected shards failed, were not READY, or returned a generation other than the catalog identity.

A failed shard is never silently omitted.

## Live synthetic canary  2026-10-24

Three disposable Neon projects were used:
- `chatgpt-0001`: projection shard;
- `hermes-0001`: projection shard;
- independent router project.

Both shard HTTPS endpoints returned `HTTP 200 / FOUND` for the shared synthetic query `copper compass`.

The canonical fresh router deployment returned:
- two-shard `FOUND` with two hits;
- provider-scoped `hermes` search querying only `hermes-0001`;
- MCP `initialize`;
- MCP `tools/list` with exactly one tool, `session_search`;
- MCP `tools/call` with the same two-shard federated result.

A deliberate unavailable-shard canary returned `PARTIAL`, preserved the healthy ChatGPT hit, and reported `hermes-0001 / QUERY_FAILED`.

A prior router slug retained stale environment behavior after an env-only rollback despite a completed deployment. A fresh immutable router slug restored deterministic `FOUND`. The stale slug is diagnostic evidence, not the canonical route.

## Arcade

The router MCP endpoint is registered as remote server `theseus-session-search`.

The dedicated Arcade gateway `Session Search` exposes one consumer tool:
`TheseusSessionSearch.session_search`.

The ChatGPT host binding is now an App-backed private plugin:
- App: `asdk_app_6adcf7fcb44c8191b4a0da4d734b91da`
- plugin: `plugin_asdk_app_6adcf7fcb44c8191b4a0da4d734b91da`
- version: `1.0.1`
- release: `pluginrel_6adcf9bf0fc48191a559c3b044dd7192`

The host-created `.app.json` binding was preserved while the checked-in
`session-search-usage` skill overlay was added. No production `mcp.json` or
`.mcp.json` was introduced.

Host execution is VERIFIED. In the selected App-backed ChatGPT plugin:
1. `Arcade_SelectTools` selected `TheseusSessionSearch.session_search`;
2. `Arcade_UseTool` executed the synthetic query `copper compass`;
3. the result was `FOUND` with both `chatgpt-0001` and `hermes-0001`;
4. `unavailable_shards=[]`;
5. the same host smoke passed again after the skill overlay update.

## Credential boundary

Both synthetic shard Functions use dedicated SQL-created
`session_search_serving` LOGIN roles.

Current readback on both shards:
- `neon_superuser` membership: false;
- `session_search_reader` membership: true;
- bounded search/status execution: true;
- direct `messages` SELECT: false;
- direct `sessions` SELECT: false.

Temporary owner-function grants used during diagnosis were removed. Both shard
Functions still returned `FOUND` after that cleanup.

A discarded API-created serving-role experiment demonstrated that Neon-managed
API roles inherit broader `neon_superuser` membership and are unsuitable for
this least-privilege serving boundary. That temporary role was deleted.

A diagnostic error once included connection details in a private Function log.
The affected credential was rotated and application logging was changed to keep
only bounded error class/code. The bundle regression and handler tests preserve
that guard.

## Terminal acceptance

VERIFIED:
- two independent Neon project shards;
- generation-scoped rebuildable projections;
- deterministic federated merge with shard/generation provenance;
- provider-scoped routing;
- live `PARTIAL` semantics with healthy-hit preservation;
- restore from injected failure back to `FOUND`;
- independent shard Functions using least-privilege serving roles;
- independent router MCP/HTTP serving plane;
- Arcade remote server and one-tool gateway;
- App-backed ChatGPT web/mobile plugin binding;
- post-overlay ChatGPT -> Arcade -> Session Search -> Neon execution;
- no MarcoPolo request-time execution.

Historical handoff receipt
`receipts/064-app-backed-host-handoff.public.json` records the earlier
pre-creation state and is intentionally retained unchanged. It is superseded by
`receipts/064-federated-serving-final.public.json`.

## Remaining production boundary

Issue #64 proves the federated serving architecture with synthetic/private-safe
data. It does **not** authorize:
- migration of real ChatGPT/Hermes/Codex corpora;
- production cutover of existing Session Search users;
- relocation of raw source authority from the durable export store;
- production exposure before edge authentication/current policy is reverified.

Terminal disposition for issue #64: `FEDERATED_SERVING_VERIFIED`.

No real user corpus content was used in this spike.
