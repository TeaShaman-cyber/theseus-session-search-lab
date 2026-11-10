# Canonical Session Search gateway

Canonical App-backed transport:

- Arcade remote server: `theseus-session-search`
- Arcade gateway: `Session Search`
- Gateway ID / slug: `gw_3L9SqeBQ5L5Cs2V1GB6yvnKhR8P`
- MCP URL: `https://api.arcade.dev/mcp/gw_3L9SqeBQ5L5Cs2V1GB6yvnKhR8P`
- Authentication for the ChatGPT App binding: OAuth
- Stable search domain tool: `TheseusSessionSearch.session_search`

Canonical remote MCP behind Arcade is the independently deployed Session Search router. The consumer App should connect through the existing Arcade App binding, not directly to an individual Neon shard and not through a standalone packaged `mcp.json`.

## Stable server and tool identity

The production gateway now exposes **two intentional consumer tools** (issue #232):
TheseusSessionSearch.session_search and TheseusSessionSearch.session_feedback.
The search identity stays stable. Before issue #232 the gateway allowed one
read-only consumer tool, so older one-tool acceptance receipts remain historical. `TheseusSessionSearch.session_search`. The gateway is an allowlist of Arcade-discovered tools; it is not a transparent proxy that automatically substitutes a newly registered sibling server/tool. Preserve both the canonical remote-server ID `theseus-session-search` and the stable qualified tool identity across router deployments.

A verified router candidate may be registered separately for diagnostics, but do not add a second Session Search tool identity to the production gateway. Production cutover changes the URI/configuration of the existing Arcade worker, then refreshes its discovered tools, so `Arcade_SelectTools` and `Arcade_UseTool` continue to address the same qualified tool.

The current Arcade REST API documents the supported in-place operations:
- `PATCH /v1/workers/{id}` — update the existing worker configuration according to the current Arcade API schema;
- `POST /v1/workers/{id}/tools/refresh` — refresh the worker's discovered tool definitions after the backend change.

After those operations, verify that the `Session Search` gateway still exposes the same qualified `TheseusSessionSearch.session_search` identity with the refreshed schema and preserves the intentional `TheseusSessionSearch.session_feedback` tool when feedback remains enabled.

In the current ChatGPT-native Arcade management surface, the exposed server actions are Add/Remove/List and do not include worker PATCH or tool refresh. Therefore an in-place URI cutover is `UNAVAILABLE` through that surface alone. Use an authenticated Arcade dashboard/API surface when available; do not silently substitute delete/re-add or a second production tool identity.

## Host surface

The Arcade gateway itself exposes the Session Search domain capability. The ChatGPT host may expose Arcade discovery/execution wrappers such as `Arcade_SelectTools` and `Arcade_UseTool`; therefore do not use total top-level host tool count as the acceptance invariant.

The invariant is that the existing App-backed host can discover and execute `TheseusSessionSearch.session_search` through the Arcade surface while preserving web/mobile compatibility.

For retrieval-policy capable deployments, fresh discovery must expose `search_mode` with `strict`, `recall`, and `auto` (default `strict`). The stable `session_search` tool returns exact-text result groups by default, preserving all matched occurrences/provenance under each group. `auto` must return a `retrieval` receipt that distinguishes requested from effective mode; a strict grouped miss may escalate to recall, while `PARTIAL` must remain visible and must not be hidden by retry.

## Historical issue232 gateway checkpoint, November 8 2026

The original Neon router URL remains unchanged; deployment #9 is completed.
All seven preexisting deployment environment keys were preserved, and one
feedback-specific database URL setting was added. Existing Arcade gateway
gw_3L9SqeBQ5L5Cs2V1GB6yvnKhR8P now lists exactly two tools,
session_search and session_feedback. A production App-backed search probe
returned FOUND across all four shards with none unavailable. The current
ChatGPT host session still selected ONLY session_search; feedback write
acceptance requires a fresh host tool discovery and genuine write/readback.
Older one-tool acceptance receipts remain historical pre-#232 evidence.

## Current grouped-default checkpoint, November 10 2026

After #264 promotion and runtime rollout:
- the production Neon router is `ssrouter2` deployment #10, active/completed;
- the existing Arcade/App-backed search identity remains `TheseusSessionSearch.session_search`;
- a fresh read-only host probe returned `FOUND`;
- the result contained `groups[]` and no legacy `hits[]`;
- four shards were queried with `unavailable_shards=[]`;
- the retrieval receipt contained `candidate_count`, `candidate_group_count`, `requested_mode`, `effective_mode`, `strict_group_count`, and `returned_group_count`;
- the gateway continues to have the two intentional consumer tools introduced by #232: `session_search` and `session_feedback`.

This is a dated production checkpoint. Re-read deployment/tool discovery when freshness matters.

## Current acceptance

Gateway/control-plane existence is not host acceptance.

A fresh host acceptance must:
1. use the existing App-backed Session Search plugin identity;
2. discover the current Session Search domain tool;
3. execute a bounded live probe;
4. receive a terminal `FOUND`, `NO_SIGNAL`, or `PARTIAL` result with explicit `queried_shards` and `unavailable_shards`;
5. preserve returned shard/generation provenance;
6. require no unavailable shards before declaring the serving path healthy;
7. for an `auto` acceptance probe, require a `retrieval` receipt with `requested_mode`, `effective_mode`, `strict_group_count`, and `returned_group_count` consistent with the observed bounded policy.

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
