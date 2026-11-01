# Canonical Session Search gateway

Canonical App-backed transport:

- Arcade remote server: `theseus-session-search`
- Arcade gateway: `Session Search`
- Gateway ID / slug: `gw_3L9SqeBQ5L5Cs2V1GB6yvnKhR8P`
- MCP URL: `https://api.arcade.dev/mcp/gw_3L9SqeBQ5L5Cs2V1GB6yvnKhR8P`
- Authentication for the ChatGPT App binding: OAuth
- Session Search domain tool: `TheseusSessionSearch.session_search`

Canonical remote MCP behind Arcade is the independently deployed Session Search router. The consumer App should connect through the existing Arcade App binding, not directly to an individual Neon shard and not through a standalone packaged `mcp.json`.

## Host surface

The Arcade gateway itself exposes the Session Search domain capability. The ChatGPT host may expose Arcade discovery/execution wrappers such as `Arcade_SelectTools` and `Arcade_UseTool`; therefore do not use total top-level host tool count as the acceptance invariant.

The invariant is that the existing App-backed host can discover and execute `TheseusSessionSearch.session_search` through the Arcade surface while preserving web/mobile compatibility.

For retrieval-policy capable deployments, fresh discovery must expose `search_mode` with `strict`, `recall`, and `auto` (default `strict`). `auto` must return a `retrieval` receipt that distinguishes requested from effective mode; a strict miss may escalate to recall, while `PARTIAL` must remain visible and must not be hidden by retry.

## Current acceptance

Gateway/control-plane existence is not host acceptance.

A fresh host acceptance must:
1. use the existing App-backed Session Search plugin identity;
2. discover the current Session Search domain tool;
3. execute a bounded live probe;
4. receive a terminal `FOUND`, `NO_SIGNAL`, or `PARTIAL` result with explicit `queried_shards` and `unavailable_shards`;
5. preserve returned shard/generation provenance;
6. require no unavailable shards before declaring the serving path healthy;
7. for an `auto` acceptance probe, require a `retrieval` receipt with `requested_mode`, `effective_mode`, and `strict_hit_count` consistent with the observed bounded policy.

The **live shard catalog** is runtime evidence and may evolve. Compare observed provenance with current deployment/project evidence when shard topology matters.

### Verified 2026-10-31 acceptance

After issue #126 promoted the accepted ChatGPT corpus into the live catalog, a fresh App-backed host smoke verified:
- `chatgpt-0001`;
- `chatgpt-0002`;
- `deepseek-0001`;
- `xai-0001`;
- terminal status `FOUND`;
- `unavailable_shards=[]`.

This is a point-in-time acceptance checkpoint, not a hard-coded future topology requirement.

## Historical #64 acceptance

The historical #64 synthetic acceptance used:
- `chatgpt-0001`;
- `hermes-0001`;
- `copper compass -> FOUND`.

That remains durable evidence that the App-backed ChatGPT path worked on the #64 synthetic topology. It is not a permanent current-shard requirement.

## Promotion boundary

This file does not authorize corpus promotion, production cutover, shard activation, credential changes, or edge-auth changes.

Do not inherit a stale promotion conclusion from historical #64 text. Verify the smallest current authoritative deployment/promotion state needed for the action being considered.

Consumer-overlay maintenance must preserve the host-created App binding and must not add `mcp.json` or `.mcp.json`.
