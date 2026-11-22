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

As verified through the 2026-11-20 production checkpoint, the existing **private** production Gateway
exposes **five** consumer tools: `TheseusSessionSearch.session_search`,
`TheseusSessionSearch.session_browse`,
`TheseusSessionSearch.session_feedback`,
`TheseusSessionSearch.session_feedback_get`, and
`TheseusSessionSearch.session_coverage`. Earlier four-tool, three-tool and
two-tool checkpoints are retained below as dated historical evidence.
The search identity stays stable. Before issue #232 the gateway allowed one
read-only consumer tool, so older one-tool acceptance receipts remain historical. `TheseusSessionSearch.session_search`. The gateway is an allowlist of Arcade-discovered tools; it is not a transparent proxy that automatically substitutes a newly registered sibling server/tool. Preserve both the canonical remote-server ID `theseus-session-search` and the stable qualified tool identity across router deployments.

Git source `c3f03696182e195d39af3a01d5ba5569b7ac09f7` (PR #277) added the fifth router MCP capability, `session_coverage`. On 2026-11-19 the existing production router was rolled forward in place, Arcade worker discovery was refreshed, and the existing Gateway was changed add-only to five tools. Fresh ChatGPT App discovery then exposed `TheseusSessionSearch.session_coverage`, and a real host call returned `status=OBSERVED`. A later config-only router deployment populated finite observed message bounds for all four active catalog generations; a broad preflight returned `catalog_bounds_complete=true`, while a ChatGPT window for 2026-11-17 through 2026-11-19 returned `OUTSIDE_OBSERVED_INDEX_RANGE` with zero selected shards. `source_currentness` deliberately remained `UNKNOWN_WITHOUT_SOURCE_WATERMARK` at that intermediate checkpoint. Later #275 source-watermark rollout added the bounded `VERIFIED_TO_PROVIDER_REPORTED_WATERMARK` / `STALE_AFTER_PROVIDER_REPORTED_WATERMARK` states while preserving fail-closed unknown. These are dated serving observations, not proof of complete upstream ChatGPT history.

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

The authorized coverage rollout established five intentional consumer tools by adding only TheseusSessionSearch.session_coverage; the prior four identities remain present. Fresh host discovery must execute a bounded coverage preflight and observe status=OBSERVED, a valid coverage.window_fit, and one defined source-currentness state. Provider-reporting exports use VERIFIED_TO_PROVIDER_REPORTED_WATERMARK / STALE_AFTER_PROVIDER_REPORTED_WATERMARK; accepted bounded provider snapshots use VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT / STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT; missing or conflicting evidence fails closed to UNKNOWN_WITHOUT_SOURCE_WATERMARK. The preflight remains metadata-only; none of these states certifies complete raw account history or SOURCE_COMPLETE.

## Verified serving refresh checkpoint — November 22 2026

Issue #304 promoted the accepted 2026-11-21 ChatGPT refresh through the same
private App/Gateway identity. Fresh host discovery and execution observed:

- the canonical remote server remains theseus-session-search;
- the production router URI returned to the preexisting ssrouter2 endpoint;
- the new ChatGPT generation pair is
  0f0153c6fbdbd747bfcef80779ebaae1327a8101d9bc3051711d038e301774c9
  and
  5004e3c94709be89ed25e1fe51ada6e8bd691a493fecf86e13b0712378ed0e8a;
- catalog_bounds_complete=true;
- a ChatGPT window ending before the provider watermark returns
  VERIFIED_TO_PROVIDER_REPORTED_WATERMARK;
- a later window returns STALE_AFTER_PROVIDER_REPORTED_WATERMARK;
- the accepted provider-export watermark is
  2026-11-19T18:43:36.744Z;
- broad and ChatGPT-scoped strict searches returned FOUND with
  unavailable_shards=[];
- a strict impossible control returned NO_SIGNAL with all selected shards
  available.

One deployment behavior is now part of the operator contract: updating router
environment alone is not sufficient proof that module-scope configuration has
reloaded. After catalog/source-watermark changes, create a fresh runtime
deployment on the same production router URI, then refresh the existing Arcade
worker tools and perform fresh App-backed coverage/search readback. Do not
create a replacement production worker/tool identity merely to clear stale
runtime or discovery state.

The source snapshot watermark and runtime deployment freshness are separate
claims. The plugin can be correctly deployed against the latest accepted export
while conversations later than that export still report stale/outside observed
coverage.

## Verified Gemini terminal checkpoint — November 22 2026

Issues #311 and #332 promoted the official Gemini Apps Activity corpus through the same production identity. Fresh App-backed readback observed five READY shards: chatgpt-0001, chatgpt-0002, deepseek-0001, xai-0001, and gemini-0001. The canonical worker remains theseus-session-search on the original ssrouter2 production URI and still exposes exactly five consumer tools.

Gemini coverage at/before the accepted Takeout request boundary returns VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT with source_scope=GEMINI_APPS_ACTIVITY. A later/open Gemini window returns STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT. Fresh Gemini strict search returns FOUND with exact gemini-0001 generation provenance and unavailable_shards=[]; a broad impossible control queries all five shards with unavailable_shards=[].

The production cutover again proved that deployment completion alone is not module-scope reload evidence. The combined source/config deployment reached completed while direct health still exposed four shards; a distinct runtime-only deployment on the same URI produced the observable five READY shard state. Arcade worker tools were then refreshed and App-backed acceptance repeated.

This is dated runtime evidence. The live catalog remains runtime state rather than a permanent constant, and the Gemini snapshot contract does not claim complete lifetime Gemini history.

## Verified private feedback readback checkpoint — November 19 2026

From canonical Git `2f2d4777995915b927e0d725502b2b8243773ea2`, native Neon
`ssrouter2` deployment #13 completed while preserving all eight environment
key names and existing authenticated routes. The existing Arcade Gateway
received **only** `session_feedback_get` as its fourth tool; all previous
three tools remained. Fresh App-host `Arcade_SelectTools` discovered the new
tool. A real `session_feedback_get(feedback_ref)` returned `FOUND` for
preexisting cross-chat feedback; independent Neon metadata matched and Grafana
Tempo linked its read span to the originating search span via stored refs.
The original live response returned `notes_present` but excluded private
`notes` text. Issue #272's subsequent Git source change allows nullable
bounded `notes` on this exact-ref private read path. **Do not claim that notes
are live until a post-deployment host readback proves them**. The original
#13 checkpoint is historical; check current deployment before every rollout.
The App identity, OAuth, feedback store and corpus routing are unchanged.

## Verified hint / browse checkpoint — November 19 2026

The existing `ssrouter2` Neon Function reached active deployment #12 from
merged main `5d5f1a2b81214084c1acb2fffdbcf11529989b03`. The existing
Arcade `Session Search` Gateway still exposes three tools. A fresh ChatGPT
App-backed `Arcade_SelectTools` read returned `session_search` with the
**optional**, separately ranked translated-query advisory and exposed both
`session_browse` and `session_feedback`. A bounded `session_search` call returned
`FOUND`, `exact_text_v1`, a `request_ref`, and no unavailable shards. No new
App binding, server identity, database store or automatic translator was added.

There was a temporary divergence between deployed router metadata and the
ChatGPT discovery description. Arcade catalog and host discovery later showed
the new description, but the exact cache refresh trigger was **not proven**.
Recheck *both* Arcade metadata and host discovery rather than assuming immediate
hot reload after every Neon deployment. This is a dated checkpoint.

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

## Historical grouped-default checkpoint, November 10 2026

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
2. discover the current Session Search domain tools rather than infer them from Git or router source;
3. verify the five intentional tools remain available: session_search, session_browse, session_coverage, session_feedback, and session_feedback_get;
4. execute a bounded coverage probe and accept the source-currentness family justified by the scoped provider: VERIFIED_TO_PROVIDER_REPORTED_WATERMARK / STALE_AFTER_PROVIDER_REPORTED_WATERMARK for provider-reporting exports, VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT / STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT for accepted bounded provider snapshots, or fail-closed UNKNOWN_WITHOUT_SOURCE_WATERMARK; none implies SOURCE_COMPLETE;
5. execute a bounded live search probe;
6. receive a terminal FOUND, NO_SIGNAL, or PARTIAL search result with explicit queried_shards and unavailable_shards;
7. preserve returned shard/generation provenance;
8. require no unavailable shards before declaring the serving path healthy;
9. for an auto acceptance probe, require a retrieval receipt with requested_mode, effective_mode, strict_group_count, and returned_group_count consistent with the observed bounded policy.

The live shard catalog is runtime evidence and may evolve. Compare observed provenance with current deployment/project evidence when shard topology matters.

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
