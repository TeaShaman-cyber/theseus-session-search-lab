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

A dedicated Arcade gateway named `Session Search` exposes exactly one tool:
`TheseusSessionSearch.session_search`.

Arcade control-plane discovery is verified. ChatGPT-host execution through the newly created gateway remains pending because the available Arcade admin surface does not expose a gateway execution call; the gateway must be attached to ChatGPT as a web MCP connection before the final host-side smoke.

## Credential boundary

The ChatGPT synthetic shard Function uses a dedicated SQL-created `session_search_serving` login that:
- can execute the bounded search function;
- cannot directly SELECT the messages table;
- is not a member of `neon_superuser`.

Neon's role-management API automatically adds API-created login roles to `neon_superuser`; such roles are not suitable for this serving boundary.

The Hermes project has the same SQL-created least-privileged serving role and its ACL was verified, but the current synthetic Function deployment uses Neon's injected owner connection because the chat tool safety layer blocked provisioning that role's connection string into Function environment. This is acceptable only for synthetic spike data. Real-corpus promotion is blocked until both shards use least-privileged serving credentials.

A live error-log incident showed that a database driver error can contain a connection URI. The exposed synthetic credential was immediately rotated, and the handler now logs only error class/code with details redacted. A regression test proves connection strings/passwords do not escape through application error logs.

## Current boundary

Verified:
- independent shard Functions;
- independent router Function;
- two-project fan-out and deterministic merge;
- scoped routing;
- live PARTIAL semantics;
- direct MCP tool execution;
- Arcade remote-server discovery and one-tool gateway composition.

Not yet verified:
- ChatGPT host -> Arcade gateway -> Session Search MCP execution;
- least-privileged Function credential on the Hermes shard;
- production authentication for the public MCP/HTTP edge;
- real-corpus migration or cutover.

No real user corpus content was used in this spike.
