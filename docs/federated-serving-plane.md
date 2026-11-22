# Independent Session Search serving plane

Issue #64 originally moved the Session Search read path out of MarcoPolo; later issues retained that boundary while promoting real corpora and the grouped-default result contract.

## Request path

```text
ChatGPT
  -> Arcade Session Search gateway
  -> Neon router Function
      -> selected READY Neon shard Functions
          -> bounded grouped search_generation_grouped_v3(...)
      -> exact-text merge across shards
      -> final grouped Top-K
```

At the verified 2026-11-10 production checkpoint the live catalog queried four shards: `chatgpt-0001`, `chatgpt-0002`, `deepseek-0001`, and `xai-0001`. Treat that list as dated runtime evidence, not a hard-coded future topology.

The governed engineering/promotion route is exe.dev first under the current cloud-shell rules. Sprites and MarcoPolo are observed-need fallbacks, not the normal production-operations path. None of these engineering runtimes is part of the deployed HTTP/MCP request path.

## Function split

The shard Function validates one bounded search request and executes exactly one versioned Postgres function call. It does not accept arbitrary SQL.

The router Function has no shard database credentials. Its catalog contains shard identity, provider, expected generation, state, and HTTPS endpoint. It selects relevant READY shards, performs HTTP fan-out, and merges results deterministically. The default result is one exact-text group per unique body; every matched occurrence keeps provider/shard/generation provenance. Equal text returned by different shards is merged before the final global limit.

The same federated search implementation serves both:
- `POST /search`;
- the MCP `session_search` tool at `POST /mcp`.

### Historical migration compatibility contract (issue #166)

This section records the pre-#264 migration sequence. It is historical evidence, not the current serving contract. #264 completed the grouped-default promotion; the current router explicitly requests grouped results and the flat v2 path remains only as a rollback/rolling-upgrade seam.

The original #166 migration candidate required:

- Requests **without** `generation_id` keep the legacy `search_v2` / ACTIVE path,
  so the deployed old router does not require an immediate change.
- An explicitly supplied, lowercase 64-hex `generation_id` selects
  `search_generation_v2(generation_id, ...)`, which retains the database READY
  and projection-schema checks. No ACTIVE fallback is allowed on this path.
- Explicit null or malformed IDs fail as `400 GENERATION_OUT_OF_BOUNDS`.
- A successful pinned response must echo the SQL-returned matching
  `generation_id` and receives additive `generation_binding=router_pinned_v1`.
  A mismatch or failed SQL call returns `503 QUERY_FAILED` without that witness.
  This witness identifies protocol behavior; it is **not** a cryptographic proof.
- A staged router (issue #166, stage 2) passes the exact per-shard catalog
  `generation_id` to the shard and requires the witness
  `generation_binding=router_pinned_v1` on every successful response.
  A shard missing the witness is reported as `PINNING_NOT_CONFIRMED` /
  `PARTIAL` even if its ACTIVE generation happens to match the catalog.
  Contradictory returned shard/provider identities also fail closed.
  `auto` never escalates `PARTIAL`; strict and recall use the same
  selected per-request catalog generation vector.
- Both source stages are **candidates only**. First deploy all transitional
  shards and verify old-router compatibility; only then consider deploying the
  new router. No catalog change, live deployment, or source promotion is
  implied by these changes.

## Time-scoped search coverage

For `POST /search` requests with `from_time` and/or `to_time`, the deployed router derives an **additive** `coverage` receipt from its existing in-memory shard catalog. The same semantics are exposed through the MCP search path. Source code remains distinct from runtime evidence: when currentness matters, verify the active deployment/App surface rather than assuming a merge is deployed.

- `window_fit`: `OUTSIDE_OBSERVED_INDEX_RANGE` only if a provider/session-filtered catalog contains eligible shards with complete, consistent numeric time bounds but the requested window excludes all of them. `OBSERVED_POSSIBLE` means some shard's *observed indexed range* intersects the query; this is **not** a guarantee that every original user conversation in that window was captured. `UNKNOWN_COVERAGE` means the provider has no eligible catalog shards, some shard bounds are unknown or invalid, or the range cannot be established.
- `source_currentness` defaults to `UNKNOWN_WITHOUT_SOURCE_WATERMARK`. A provider-scoped catalog may opt into a bounded source watermark only when every eligible shard carries the same `source_watermark` binding: finite `observed_through`, `status=PROVIDER_REPORTED_NO_KNOWN_GAPS`, `basis=provider_export_manifest`, and one lowercase 64-hex `accepted_set_sha256`. Missing, malformed, mixed-provider, or mismatched bindings fail closed to `UNKNOWN_WITHOUT_SOURCE_WATERMARK`.
- When that binding is present, a request ending at or before `observed_through` returns `VERIFIED_TO_PROVIDER_REPORTED_WATERMARK`; an open-ended request or one extending later returns `STALE_AFTER_PROVIDER_REPORTED_WATERMARK`. The additive public `source_watermark` object exposes only `observed_through`, `status`, and `basis`; the accepted-set digest remains an internal catalog binding.
- `PROVIDER_REPORTED_NO_KNOWN_GAPS` is deliberately weaker than `SOURCE_COMPLETE`: it records what the provider export manifest reported at capture time. It does not upgrade undocumented provider-export fields into a guarantee that every original account conversation exists in the export.
- Production may supply these bindings through the additive `SESSION_SEARCH_SOURCE_WATERMARKS` provider map. The router validates that map and overlays it onto the already parsed `SESSION_SEARCH_CATALOG` in memory; it does not require rewriting shard URLs or other catalog fields. Unknown providers, malformed metadata, extra watermark fields, or invalid accepted-set digests fail startup closed. Omitting the variable preserves the existing catalog object and UNKNOWN behavior.
- Bounded provider-export snapshots are a separate, weaker evidence class. Production may supply an accepted Gemini Apps Activity snapshot through `SESSION_SEARCH_SOURCE_SNAPSHOTS`; the exact binding is `source_scope=GEMINI_APPS_ACTIVITY`, finite `observed_through`, `basis=provider_takeout_request_receipt`, and one lowercase 64-hex `accepted_set_sha256`. The router never maps this evidence to `PROVIDER_REPORTED_NO_KNOWN_GAPS`.
- For provider-scoped Gemini queries, `to_time <= observed_through` returns `VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT`; an open-ended query or `to_time > observed_through` returns `STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT`. Public `source_snapshot` exposes only `source_scope`, `observed_through`, and `basis`; the accepted-set digest remains an internal binding. Missing, malformed, mixed, or conflicting bindings fail closed to unknown currentness.
- `eligible_shard_count` excludes only provider/session scopes, whereas `selected_shard_count` also applies time filtering. `catalog_observed_max_time` is the maximum finite catalog `time_max` of eligible shards (or null), and `catalog_bounds_complete` tells whether *all* eligible shards have usable min/max bounds.
- A zero-selected-shard query preserves existing `status: NO_SIGNAL` for backward compatibility but **must not** be treated as a fully searched miss; inspect `coverage.window_fit` and `queried_shards`. A queried clean miss may still return `NO_SIGNAL` with unknown source currentness, and `PARTIAL` still denotes unavailable shards.
- Non-time-scoped queries retain the previous response shape (no `coverage` property).

The additive `coverage` on a search response remains an *after-query transparency improvement*. For a preventive check before lexical retrieval, use the separate `session_coverage` preflight below. Provider-watermark acquisition remains a distinct ingestion/currentness concern tracked through [#275](https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/275) and refresh work under [#3](https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/3).

## Coverage preflight API and MCP

A time-window preflight can run **without fetching conversation text or dispatching shard searches**. The router adds a protected `POST /coverage` JSON endpoint and a conditional MCP `session_coverage` tool, both using the existing router bearer authentication and the **existing** catalog. They do not require a new Neon table, upstream API or source-ingestion privilege.

Request is an object with optional `provider` and `session_id` and **at least one** numeric epoch-seconds bound `from_time` or `to_time`. Unknown input fields, missing time bounds, invalid types and inverted ranges fail validation. Intended agent usage for questions about recent ChatGPT conversation history: obtain a time-bounded `session_coverage` receipt **before** lexical `session_search` and treat `OUTSIDE_OBSERVED_INDEX_RANGE` as a reason not to waste a lexical query for that scoped window, unless the user is also asking about older evidence.

A response has `status: OBSERVED`, `request_ref`, `observed_at` (the server's response time, **not** the last source refresh), the same additive `coverage` summary used for time-scoped search, and `catalog_generations[]` (first 32 entries; `catalog_generations_truncated` signals remaining entries, while summary counts include all eligible shards; bounded metadata: shard/provider/generation/state/observed time bounds; no endpoint URLs, credentials, source text or conversation IDs). Missing bounds or provider/shard data always cause `UNKNOWN_COVERAGE`. `source_currentness` stays `UNKNOWN_WITHOUT_SOURCE_WATERMARK` unless the provider-scoped eligible catalog is bound to one verified provider-export watermark as described above; a green `/health`, READY state, recent `time_max`, or successful preflight is never enough by itself. Even with a verified binding, `PROVIDER_REPORTED_NO_KNOWN_GAPS` remains provider-reported evidence rather than a certification that the original ChatGPT account history is complete.

**Host boundary:** Arcade's production gateway is a tool allowlist, so source `tools/list` and the App-facing tool set remain separate claims. As of the verified 2026-11-20 checkpoint, `TheseusSessionSearch.session_coverage` is discoverable and executable through the App-backed ChatGPT path; a live read-only canary returned `OBSERVED` and a bounded coverage/currentness receipt without transcript retrieval. Future router/Gateway changes still require authenticated target/tool readback rather than assuming a source merge changed the host.

## Failure semantics

- `FOUND`: every selected shard responded and at least one result group exists.
- `NO_SIGNAL`: every selected shard responded and no result group exists.
- `PARTIAL`: one or more selected shards failed, were not READY, or returned a generation other than the catalog identity.

A failed shard is never silently omitted. Healthy groups remain available under `PARTIAL`. The old flat `hits[]` shape is retained only behind the internal `flat_v1` rollback setting; it is not a public MCP option.

## Historical live synthetic canary — 2026-10-24

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

Historical canaries showed that a router slug could retain stale environment
behavior after an env-only deployment despite a completed control-plane status.
Issue #304 reproduced the mechanism more precisely on the production path:
module-scope catalog state remained stale until a fresh runtime deployment was
created. The canonical data-only route therefore preserves the production
router identity/URI, updates only the intended environment values, forces a
fresh runtime deployment of the existing accepted bundle/runtime, refreshes
Arcade worker discovery, and requires App-backed readback. Creating a new
production router slug is not the normal cache-refresh mechanism.

See [federated serving promotion runbook](federated-serving-promotion-runbook.md).

## Arcade

The router MCP endpoint is registered as remote server `theseus-session-search`.

At the verified 2026-11-20 checkpoint, the dedicated Arcade gateway `Session Search` exposed five intentional consumer tools: `TheseusSessionSearch.session_search`, `TheseusSessionSearch.session_browse`, `TheseusSessionSearch.session_coverage`, `TheseusSessionSearch.session_feedback`, and `TheseusSessionSearch.session_feedback_get`. These names are dated runtime evidence rather than a promise that every future deployment must expose the same count; current tool availability is read back through discovery when it matters.

This is a stable qualified tool identity. The Arcade gateway is an allowlist of discovered tool definitions, not a transparent proxy for all tools from every registered remote server. Router upgrades therefore keep both the canonical Arcade remote-server ID `theseus-session-search` and `TheseusSessionSearch.session_search` stable. A separately registered candidate server may be used for diagnostics, but must not be promoted by adding a version-suffixed sibling tool to the production gateway.

The supported in-place Arcade control-plane shape is to update the existing worker with `PATCH /v1/workers/{id}`, refresh discovery with `POST /v1/workers/{id}/tools/refresh`, then verify the gateway still exposes the same qualified `session_search` tool with the refreshed schema and preserves the intentional `session_feedback` tool when that capability remains enabled. If the active management surface does not expose worker PATCH/refresh and no authenticated dashboard/API route is available, classify cutover as `UNAVAILABLE` rather than replacing the tool identity.

The ChatGPT host binding is an App-backed private plugin with stable identities:
- App: `asdk_app_6adcf7fcb44c8191b4a0da4d734b91da`
- plugin: `plugin_asdk_app_6adcf7fcb44c8191b4a0da4d734b91da`

Plugin release/version is runtime evidence and must be read back when it matters; do not treat the historical bootstrap release as a current acceptance constant.

The host-created `.app.json` binding was preserved while the checked-in
`session-search-usage` skill overlay was added. No production `mcp.json` or
`.mcp.json` was introduced.

Host execution is VERIFIED. In the selected App-backed ChatGPT plugin:
1. `Arcade_SelectTools` selected `TheseusSessionSearch.session_search`;
2. `Arcade_UseTool` executed the synthetic query `copper compass`;
3. the result was `FOUND` with both `chatgpt-0001` and `hermes-0001`;
4. `unavailable_shards=[]`;
5. the same host smoke passed again after the skill overlay update.


## Historical production checkpoint — 2026-11-10

Verified after #264:
- production router `ssrouter2` deployment #10 is active/completed;
- the App-backed `TheseusSessionSearch.session_search` path returns exact-text `groups[]` by default;
- the same probe had no legacy `hits[]`;
- four shards were queried and `unavailable_shards=[]`;
- the grouped retrieval receipt exposes `candidate_count`, `candidate_group_count`, `requested_mode`, `effective_mode`, `strict_group_count`, and `returned_group_count`;
- flat output remains an internal rollback/replay and rolling-upgrade seam, not a public MCP option.

This checkpoint proves the observed serving state only; live topology and deployment numbers must still be re-read when currentness matters.

## Current production checkpoint — 2026-11-22

Verified after issues #311 and #332:

- the canonical Arcade worker remains theseus-session-search and still points to the original production ssrouter2 URI; no replacement App, worker, gateway, or tool identity was introduced;
- production router deployment #26 is active/current completed after the additive catalog update and a separate runtime-only reload;
- the live production catalog has five READY shards: chatgpt-0001, chatgpt-0002, deepseek-0001, xai-0001, and gemini-0001;
- Gemini is served from generation 88b7ccc6ff344cfd93cfef354e951446dfd0f5dd333c508fde91f2e51bdcffc6 with finite observed bounds 1742614863 through 1795274920;
- ChatGPT provider-scoped coverage keeps the accepted provider watermark semantics: VERIFIED_TO_PROVIDER_REPORTED_WATERMARK at/before its accepted export watermark and STALE_AFTER_PROVIDER_REPORTED_WATERMARK beyond it;
- Gemini provider-scoped coverage uses the weaker bounded export-snapshot contract: VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT at/before the Gemini Apps Activity Takeout request boundary and STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT beyond it;
- fresh App-backed Gemini strict search returns FOUND with exact Gemini generation provenance and unavailable_shards=[];
- a broad impossible strict control queries all five intended shards and returns NO_SIGNAL with unavailable_shards=[];
- separate ChatGPT, DeepSeek, and xAI controls remain healthy;
- the pre-Gemini four-shard cold router is retained as rollback evidence and is not the current production catalog.

The production reload also re-confirmed an operator invariant: control-plane completed on a combined source/config deployment is not proof that module-scope router configuration reloaded. The catalog became observable at the original URI only after a distinct runtime-only deployment. Always verify direct health/runtime state, then refresh the existing Arcade worker tools and perform fresh App-backed readback.

Deployment numbers and generation IDs above are dated runtime evidence, not reusable constants. The operational sequence remains versioned in federated-serving-promotion-runbook.md.

## Previous production checkpoint — 2026-11-20

Verified after #275, #284, and #286:

- App-backed `session_coverage` executes against the live router before search and reports observed index-window plus provider-watermark currentness;
- provider-scoped source-watermark bindings distinguish `VERIFIED_TO_PROVIDER_REPORTED_WATERMARK`, `STALE_AFTER_PROVIDER_REPORTED_WATERMARK`, and `UNKNOWN_WITHOUT_SOURCE_WATERMARK` without claiming `SOURCE_COMPLETE`;
- protected repository changes are classified by `.github/workflows/review-gate.yml` and wait for exact-head Codex `CLEAN`; ordinary low-risk changes do not inherit that blocking gate;
- production router Function `ssrouter2` deployment #20 was active/current completed on Node.js 24; the router code came from merged source `d619865fc6e30e9baa8d77a63c9cd9c47b057d29`, and the service-version telemetry label was corrected to that exact Git SHA;
- the router emits a narrow PostHog semantic lane for search outcomes, coverage classifications, and feedback, with strict properties, person-profile processing disabled, and bounded failure-isolated delivery;
- a live `session_coverage` request was correlated by `request_ref` to a `session coverage classified` PostHog event carrying the exact deployed service-version SHA; its technical event-level person UUID had no matching PostHog person profile row; a live strict search remained `FOUND` across both selected ChatGPT shards with none unavailable.

### Observability authority boundary

- **OpenTelemetry / Grafana:** runtime request and shard traces/metrics.
- **PostHog semantic events:** low-latency bounded service outcomes; useful for product-style longitudinal analysis, but not request or corpus authority.
- **PostHog GitHub warehouse (`sessionsearch`):** delayed development/CI/review history; useful for drift and recurring-pattern analysis, but it can be stale independently of GitHub or the runtime.
- **GitHub exact head / Neon current deployment / accepted source evidence:** remain the appropriate operational and evidence authorities for their respective claims.

Do not infer success from an empty observability surface. `STALE`, `NO_SIGNAL`, and `UNAVAILABLE` are distinct states, and semantic-event delivery failure is deliberately non-fatal to Session Search serving.

## HTTP authentication boundary

The public serving endpoints are fail-closed even when reached outside Arcade:

- router `POST /search` and `POST /mcp` require `SESSION_SEARCH_ROUTER_AUTH_TOKEN`;
- router-to-shard fan-out uses the separate `SESSION_SEARCH_SHARD_AUTH_TOKEN`;
- shard `POST /search` requires that shard token before query execution;
- `GET /health` remains unauthenticated and returns no corpus content;
- missing or invalid bearer credentials return `401 UNAUTHORIZED` before telemetry export or shard dispatch.

The ChatGPT-facing Arcade remote server supplies the router bearer from a
control-plane secret-backed `Authorization` header. The literal credential
must not appear in repository files, endpoint URLs, logs, or tool output.
Any candidate or duplicate remote-server registration that can resolve to the
same protected router must either carry the same secret-backed header or remain
disabled from production discovery.

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

## Historical #64 terminal acceptance

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

## Historical #64 production boundary

At #64 completion, the following boundary still applied. Later issues promoted real corpora and the grouped-default serving path; keep this section only as historical scope for #64.

Issue #64 proves the federated serving architecture with synthetic/private-safe
data. It does **not** authorize:
- migration of real ChatGPT/Hermes/Codex corpora;
- production cutover of existing Session Search users;
- relocation of raw source authority from the durable export store;
- production exposure before edge authentication/current policy is reverified.

Terminal disposition for issue #64: `FEDERATED_SERVING_VERIFIED`.

No real user corpus content was used in this spike.
