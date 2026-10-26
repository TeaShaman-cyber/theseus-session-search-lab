---
name: session-search-usage
description: Use whenever historical ChatGPT, Hermes, Codex, or other accepted conversation history is searched through the custom Arcade-backed Session Search plugin, or when diagnosing the ChatGPT-to-Arcade-to-Session-Search serving path.
---

# Session Search through Arcade in ChatGPT

This plugin routes:

```text
ChatGPT web/mobile
  -> existing App-backed Session Search plugin
  -> Arcade host surface
       -> Arcade_SelectTools
       -> Arcade_UseTool
  -> TheseusSessionSearch.session_search
  -> federated Session Search router
  -> current live shard catalog
```

Session Search is a read-only retrieval surface in this plugin. Ingest, rebuild, generation activation, corpus mutation, and recovery stay outside the consumer plugin.

## Web/mobile compatibility invariant

Preserve the host-created `.app.json` App binding.

Do **not** add `mcp.json` or `.mcp.json` to the production App-backed plugin. A standalone package that embeds a remote MCP endpoint is not the production web/mobile path for this project.

Do not replace the existing App/plugin identity during an overlay refresh. For App-backed plugin updates, preserve the existing App binding, MCP configuration, name, and presentation and use a guarded current-release update.

Read `references/gateway.md` before diagnosing routing or changing the connection.

## Search semantics

The Session Search domain capability is `TheseusSessionSearch.session_search`.

In the ChatGPT host, that domain tool may be discovered/executed through the Arcade wrapper surface rather than appearing as a raw top-level tool. Use `Arcade_SelectTools` to discover the exact current tool contract when needed, then execute it through `Arcade_UseTool`.

Start with the smallest useful query:
- `query` is required;
- use `provider` only when the user clearly scopes the search and that provider is supported by the current serving/catalog evidence;
- do not infer that historical #64 providers are present in the current live shard catalog;
- use `session_id` or time bounds only when they materially narrow retrieval;
- use `scopes` deliberately: `dialogue`, `evidence`, or `trace`;
- keep `limit` bounded and expand only when the first retrieval is insufficient.

Do not silently translate `PARTIAL` into `NO_SIGNAL`.

Interpret terminal states as:
- `FOUND`: all selected READY shards responded and at least one hit exists;
- `NO_SIGNAL`: all selected READY shards responded and no hit exists;
- `PARTIAL`: at least one relevant shard failed, was non-READY, or returned a stale generation.

When `PARTIAL`, report the healthy evidence and the unavailable shard(s). Do not claim that missing shards contained no relevant history.

Preserve returned shard and generation provenance when it matters to the answer or diagnosis.

## Live shard catalog

Treat each call's `queried_shards` and `unavailable_shards` as direct serving evidence for that invocation.

The live shard catalog may evolve independently of the consumer skill. Therefore:
- do not hard-code one provider/shard pair as the universal current topology;
- do not use absence from the live shard catalog as proof that the underlying historical corpus does not exist elsewhere;
- do not substitute a different Session Search surface when reconstructing historical behavior without stating the surface change;
- compare live provenance with current deployment/issue evidence when topology matters.

Point-in-time observation on 2026-10-26 showed `deepseek-0001` and `xai-0001` in the live catalog. That observation is evidence, not a permanent acceptance constant.

## Historical #64 acceptance

The historical #64 host acceptance used synthetic `chatgpt-0001` and `hermes-0001` shards and required `copper compass -> FOUND` from both. That smoke proved the App-backed host path at that point in time.

It is **historical #64** evidence, not the current universal topology contract.

## Authority boundary

The searchable Neon state is a rebuildable derived projection, not raw-history authority.

Current architecture:

```text
durable accepted source exports
  -> ingest / normalize / verify
  -> federated Neon projections
  -> independent read-only serving plane
  -> Arcade
  -> ChatGPT
```

MarcoPolo may be used for engineering, ingest, QA, rebuild, recovery, and current deployment verification. It is not part of the production read request path.

A successful live query proves only the queried serving surface and shards. It does not prove completeness of every provider corpus.

## Safety and privacy

Never expose database connection strings, passwords, OAuth tokens, Authorization headers, private Drive identifiers, or internal source URLs in chat, plugin files, logs, receipts, or issue comments.

Database/runtime errors can contain connection details. Log only bounded error class/code unless a sanitized detail field is explicitly verified safe.

This consumer overlay does not authorize corpus promotion, generation activation, router cutover, or a change in edge authentication. Those remain separate operational decisions requiring current evidence.

## Failure isolation

Keep these layers separate:

1. ChatGPT plugin exposure and selected App binding;
2. ChatGPT-to-Arcade OAuth/session;
3. Arcade wrapper/discovery surface;
4. registered Session Search remote MCP server;
5. router MCP/HTTP behavior;
6. current live shard catalog;
7. individual Neon shard Function;
8. bounded Postgres query plane and active generation.

A failure at one layer is evidence about that layer only. Preserve `UNKNOWN`, `DEGRADED`, or `REPROBE_REQUIRED` when the next boundary has not been observed.

## Final host smoke

After updating the existing App-backed plugin:

1. start a fresh ChatGPT conversation on the intended host surface;
2. select the existing Session Search App-backed plugin; do not create a replacement identity;
3. use `Arcade_SelectTools` to confirm the Session Search domain capability resolves to `TheseusSessionSearch.session_search`;
4. use `Arcade_UseTool` to run a bounded safe probe such as `copper compass`;
5. require a contract-valid terminal status and explicit `queried_shards` / `unavailable_shards`;
6. require `unavailable_shards=[]` before calling the serving path healthy;
7. if the result is `PARTIAL`, classify the host path as executable but serving as DEGRADED;
8. compare observed provenance with the current live shard catalog/deployment evidence instead of historical #64 shard names.

Do not infer host success from Arcade control-plane state alone. Do not infer corpus absence merely because a provider/shard family is not present in the current live catalog.
