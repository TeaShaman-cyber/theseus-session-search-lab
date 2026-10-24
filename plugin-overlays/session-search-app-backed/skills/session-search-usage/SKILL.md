---
name: session-search-usage
description: Use whenever historical ChatGPT, Hermes, Codex, or other accepted conversation history is searched through the custom Arcade-backed Session Search plugin, or when diagnosing the ChatGPT-to-Arcade-to-Session-Search serving path.
---

# Session Search through Arcade in ChatGPT

This plugin routes:

```text
ChatGPT
  -> App-backed Session Search plugin
  -> Arcade Session Search gateway
  -> TheseusSessionSearch.session_search
  -> federated Session Search router
  -> derived Neon projection shards
```

Session Search is a read-only retrieval surface in this plugin. Ingest, rebuild, generation activation, corpus mutation, and recovery stay outside the consumer plugin.

## Web/mobile compatibility invariant

Preserve the host-created `.app.json` App binding.

Do **not** add `mcp.json` or `.mcp.json` to the production App-backed plugin. A standalone package that embeds a remote MCP endpoint is not the production web/mobile path for this project.

Read `references/gateway.md` before diagnosing routing or changing the connection.

## Search semantics

Use the single exposed tool `TheseusSessionSearch.session_search`.

Start with the smallest useful query:
- `query` is required;
- use `provider` only when the user clearly scopes the search to a known source such as ChatGPT or Hermes;
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

MarcoPolo may be used for engineering, ingest, QA, rebuild, or recovery. It is not part of the production read request path.

## Safety and privacy

Never expose database connection strings, passwords, OAuth tokens, Authorization headers, private Drive identifiers, or internal source URLs in chat, plugin files, logs, receipts, or issue comments.

Database/runtime errors can contain connection details. Log only bounded error class/code unless a sanitized detail field is explicitly verified safe.

The current synthetic spike does not authorize real-corpus promotion. Before any real corpus is served, verify current edge authentication, least-privilege shard credentials, and the intended production cutover.

## Failure isolation

Keep these layers separate:

1. ChatGPT plugin exposure and selected App binding;
2. ChatGPT-to-Arcade OAuth/session;
3. Arcade gateway and one-tool exposure;
4. registered Session Search remote MCP server;
5. router MCP/HTTP behavior;
6. individual Neon shard Function;
7. bounded Postgres query plane and active generation.

A failure at one layer is evidence about that layer only. Preserve `UNKNOWN`, `DEGRADED`, or `REPROBE_REQUIRED` when the next boundary has not been observed.

## Final host smoke

After creating or updating the App-backed plugin:

1. start a fresh ChatGPT conversation;
2. select the Session Search plugin;
3. verify exactly the expected Session Search tool surface is exposed;
4. run the synthetic query `copper compass`;
5. require `FOUND` with both `chatgpt-0001` and `hermes-0001` provenance before declaring the host cutover verified.

Do not infer host success from Arcade control-plane state alone.
