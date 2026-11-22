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

Session Search exposes read-only retrieval, exact-reference feedback readback and an append-only feedback writer scoped to a separate database, when each tool is available in current host discovery. Ingest, rebuild, generation activation, corpus mutation, and recovery remain outside the consumer plugin.

## Web/mobile compatibility invariant

Preserve the host-created `.app.json` App binding.

Do **not** add `mcp.json` or `.mcp.json` to the production App-backed plugin. A standalone package that embeds a remote MCP endpoint is not the production web/mobile path for this project.

Do not replace the existing App/plugin identity during an overlay refresh. For App-backed plugin updates, preserve the existing App binding, MCP configuration, name, and presentation and use a guarded current-release update.

Read `references/gateway.md` before diagnosing routing or changing the connection.

## Search semantics

The Session Search domain capability is `TheseusSessionSearch.session_search`.

Treat that name as the **stable qualified tool identity**. The Arcade gateway is an allowlist of concrete discovered tool definitions, not a transparent proxy for every tool on every registered remote server. A new router deployment must therefore replace the backend URI beneath the canonical Arcade remote server `theseus-session-search`, then refresh that worker's tool discovery, while the gateway continues to expose `TheseusSessionSearch.session_search`.

Do not solve a router cutover by adding a second version-suffixed Session Search tool to the production gateway. Candidate remote servers may exist for isolated diagnostics, but they remain outside the production gateway until the canonical remote-server URI is updated.

In the ChatGPT host, that domain tool may be discovered/executed through the Arcade wrapper surface rather than appearing as a raw top-level tool. Use `Arcade_SelectTools` to discover the exact current tool contract when needed, then execute it through `Arcade_UseTool`.

The normal result shape is grouped by exact text. One body occupies one result slot; `representative` carries the best-ranked occurrence context and `occurrences[]` preserves every matched historical occurrence/provenance, including provider, shard, and generation when present. Do not treat one grouped body as one historical event.

When the current tool contract exposes `search_mode`, use its explicit retrieval policy rather than trying to simulate recall by rewriting the query:
- `strict` is the default and preserves the bounded lexical AND-style behavior;
- `recall` broadens lexical candidate retrieval and remains candidate evidence rather than semantic authority;
- `auto` is bounded policy: run strict first and escalate only after a clean strict `NO_SIGNAL`; it must not hide or retry a `PARTIAL` result;
- a clean strict+recall miss remains `NO_SIGNAL` / `UNKNOWN_WITHIN_CURRENT_SURFACE`, not proof that history is absent.

For `auto`, inspect the returned `retrieval` receipt. At minimum preserve `requested_mode`, `effective_mode`, `strict_group_count`, `candidate_count`, `candidate_group_count`, and `returned_group_count`. A claim that auto recall helped is justified only when the receipt shows `requested_mode=auto`, `strict_group_count=0`, and `effective_mode=recall`.

Start with the smallest useful query:
- `query` is required;
- use `provider` only when the user clearly scopes the search and that provider is supported by the current serving/catalog evidence;
- do not infer that historical #64 providers are present in the current live shard catalog;
- use `session_id` or time bounds only when they materially narrow retrieval;
- use `scopes` deliberately: `dialogue`, `evidence`, or `trace`;
- keep `limit` bounded and expand only when the first retrieval is insufficient.

## Time-window coverage preflight

When current host discovery exposes `TheseusSessionSearch.session_coverage` and the user's request is materially time-scoped, prefer that cheap read-only preflight **before** `session_search`. It reads the router's current shard catalog and performs no lexical shard dispatch.

Pass only the needed provider, session_id, from_time, and/or to_time; at least one time bound is required.

Interpret coverage.window_fit first:
- OUTSIDE_OBSERVED_INDEX_RANGE: the requested window does not intersect any eligible shard's complete observed indexed bounds. Skip a lexical search for that exact window unless the user also wants older analogous material.
- OBSERVED_POSSIBLE: the indexed catalog overlaps the requested window. This says only that a search may be worthwhile; it does not prove complete upstream history.
- UNKNOWN_COVERAGE: provider/session coverage or catalog bounds are insufficient. Preserve uncertainty instead of asserting absence.

Interpret source_currentness separately from observed index bounds:

- UNKNOWN_WITHOUT_SOURCE_WATERMARK: no single verified accepted-set-bound source evidence contract applies coherently to every eligible shard. A green router, READY generation, or recent time_max does not change this.
- VERIFIED_TO_PROVIDER_REPORTED_WATERMARK: every eligible shard for the explicitly scoped provider is bound to the same accepted set and provider-reporting export watermark, and the requested window ends at or before that watermark.
- STALE_AFTER_PROVIDER_REPORTED_WATERMARK: the same watermark binding exists, but the request is open-ended or extends later than the watermark.
- VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT: every eligible shard is bound to the same accepted bounded provider snapshot, and the requested window ends at or before source_snapshot.observed_through. The current Gemini Apps Activity source uses this state.
- STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT: the same snapshot binding exists, but the request is open-ended or extends later than source_snapshot.observed_through.

When present, source_watermark.status=PROVIDER_REPORTED_NO_KNOWN_GAPS means the provider export manifest reported no known incomplete corpora / inventory warnings / omissions / redactions at capture time. Treat that as bounded provider-reported evidence, not as an unconditional claim that the original account history is complete. source_snapshot is deliberately weaker: for Gemini it describes the accepted GEMINI_APPS_ACTIVITY Takeout surface and its conservative request-time boundary, not all Gemini conversations ever created. observed_at remains the router response time. Bounded generation metadata describes the serving catalog observed for that preflight, not raw-history authority.

If session_coverage is absent from fresh Arcade/host discovery, do not invent or assume it from Git source. Fall back to a time-scoped `session_search` only when that search is still useful, inspect its additive `coverage` receipt, and state that preventive preflight is unavailable on the current surface.

Do not silently translate `PARTIAL` into `NO_SIGNAL`.

Interpret terminal states as:
- `FOUND`: all selected READY shards responded and at least one exact-text result group exists;
- `NO_SIGNAL`: all selected READY shards responded and no result group exists;
- `PARTIAL`: at least one relevant shard failed, was non-READY, or returned a stale generation.

When `PARTIAL`, report the healthy evidence and the unavailable shard(s). Do not claim that missing shards contained no relevant history.

Preserve returned shard and generation provenance when it matters to the answer or diagnosis. When a group has multiple occurrences, inspect `occurrences[]` rather than reporting only the representative as if the other historical appearances did not exist.

## Read an identified conversation

When the task requires more than matched snippets, use the companion
`session-browse-usage` skill. Carry an actual `session_id`, `shard_id` and
`generation_id` from an observed search occurrence into `session_browse`;
never infer that a partial indexed slice covers the full original history.

## App-only feedback (issue #232)

The canonical remote MCP server supports the bounded append-only
`TheseusSessionSearch.session_feedback`. A later verified **2026-11-19**
ChatGPT App host checkpoint exposed all **four** tools: `session_search`,
`session_browse`, `session_feedback` and `session_feedback_get`, following
existing Neon router deployment #13 and an additive Arcade Gateway change.
Tool presence remains runtime-scoped: discover the current schema before
invoking any tool, and do not claim an accepted feedback write until actual
operation and independent Neon record readback succeed.

When feedback is discoverable, use only baseline_request_ref,
optional result_request_ref, failure_class, operator name/version,
outcome, and optional notes. Never include raw query or target text.
App feedback writes exclusively to feedback_issue232, and a
separate server agent stores local feedback independently.
There is no automatic synchronization, scheduled transfer or
automatic ranking-policy promotion. See docs/feedback-sync.md.

For readback, call `TheseusSessionSearch.session_feedback_get` with an **exact**
recorded 32-hex lowercase `feedback_ref` from the writing agent or an earlier
private chat. A `FOUND` response carries `received_at`, baseline/result
`request_ref`, failure class, operator, outcome and `notes_present`. The
issue #272 updated **Git source contract** also returns the original nullable
`notes`, bounded to 4000 characters, through the same authenticated private
App. Never treat a missing record (`NOT_FOUND`), tool error or old deployed
`notes_present`-only response as successful full diagnostic readback. Never
publish private notes in public issues or include them in tracing/metrics.
Use the `request_ref` to correlate feedback with the originating search span
(`session_search.request_ref`) in Tempo; a trace match is retention-scoped and
must be independently observed when needed.

When only the search tool is selected after a gateway change,
classify APP_TOOL_REPROBE_REQUIRED and use a fresh existing
ChatGPT App session; do not invent a replacement App or gateway.

## Live shard catalog

Treat each call's `queried_shards` and `unavailable_shards` as direct serving evidence for that invocation.

The live shard catalog may evolve independently of the consumer skill. Therefore:
- do not hard-code one provider/shard pair as the universal current topology;
- do not use absence from the live shard catalog as proof that the underlying historical corpus does not exist elsewhere;
- do not substitute a different Session Search surface when reconstructing historical behavior without stating the surface change;
- compare live provenance with current deployment/issue evidence when topology matters.

Point-in-time observation on 2026-10-26 showed `deepseek-0001` and `xai-0001` in the live catalog. A later verified acceptance on 2026-10-31, after issue #126 promotion, observed `chatgpt-0001`, `chatgpt-0002`, `deepseek-0001`, and `xai-0001` with `unavailable_shards=[]` through the App-backed host path. These observations are evidence, not permanent acceptance constants.

A fresh terminal production acceptance on 2026-11-22 after issues #311/#332 observed five READY shards through the same App-backed identity: chatgpt-0001, chatgpt-0002, deepseek-0001, xai-0001, and gemini-0001, with unavailable_shards=[] on the broad impossible control. Gemini uses bounded provider-export snapshot currentness rather than the ChatGPT provider watermark contract. This is dated runtime evidence; continue to trust each call's queried_shards/unavailable_shards over hard-coded topology.

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

The governed engineering and promotion route is exe.dev first, using the current project/cloud-shell rules. Sprites and MarcoPolo are fallbacks only after observed exe.dev insufficiency or capability mismatch. None of those engineering runtimes is part of the production read request path.

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
3. use Arcade_SelectTools to confirm all five expected Session Search tools are discoverable: session_search, session_browse, session_feedback, session_feedback_get, and session_coverage;
4. use Arcade_UseTool to run a bounded session_coverage probe appropriate to the provider's independently verified source evidence: provider-reporting watermark or bounded provider export snapshot; require status=OBSERVED, a valid window_fit, the matching verified currentness state, bounded source metadata, and no transcript content in the receipt;
5. run a paired later/open session_coverage probe and require the matching stale state: STALE_AFTER_PROVIDER_REPORTED_WATERMARK or STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT; do not reinterpret either evidence family as SOURCE_COMPLETE;
6. use Arcade_UseTool to run a bounded safe session_search strict control probe;
7. confirm the current search schema exposes search_mode = strict|recall|auto with default strict;
8. run a bounded search_mode=auto probe that is known to strict-miss and require a retrieval receipt showing bounded strict-to-recall escalation;
9. require a contract-valid terminal search status and explicit queried_shards / unavailable_shards;
10. require unavailable_shards=[] before calling the serving path healthy;
11. if the search result is PARTIAL, classify the host path as executable but serving as DEGRADED and do not escalate it through auto;
12. compare observed provenance with the current live shard catalog/deployment evidence instead of historical #64 shard names.

Do not infer host success from Arcade control-plane state alone. Do not infer corpus absence merely because a provider/shard family is not present in the current live catalog.
