# Session Search App-backed overlay

This directory is the public, reproducible skill/support overlay for the private App-backed Session Search ChatGPT plugin created from the canonical Arcade gateway.

Desired production path:

```text
ChatGPT web/mobile
  -> existing App-backed Session Search plugin
  -> Arcade Session Search gateway
  -> TheseusSessionSearch.session_search
  -> federated Session Search router
  -> current live shard catalog
```

The ChatGPT App binding itself is private host state and is deliberately not stored in this repository.

## Current source contract and dated runtime evidence — issues #272 and #275

As verified on **2026-11-19 after Neon deployment #13**, the existing
private App-backed Arcade Gateway and ChatGPT host exposed four concrete tools:
`session_search`, `session_browse`, `session_feedback` and
`session_feedback_get`. Later coverage rollout under #275 added
`session_coverage` as the fifth tool through the same stable App/Gateway
identity; fresh host discovery executed a real read-only `OBSERVED` preflight.
Current coverage guidance preserves both bounded evidence families: VERIFIED_TO_PROVIDER_REPORTED_WATERMARK / STALE_AFTER_PROVIDER_REPORTED_WATERMARK for provider-reporting exports, and VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT / STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT for accepted provider snapshots such as Gemini Apps Activity. Missing or conflicting evidence fails closed to UNKNOWN_WITHOUT_SOURCE_WATERMARK; none is a SOURCE_COMPLETE claim.
The feedback read-only method accepts an exact `feedback_ref` on the same
authenticated private App, returning metadata and its original nullable, bounded
`notes` when the updated router source is promoted. The original production
readback before that follow-up showed `notes_present` only; keep dated runtime
receipts separate from the current source contract.

Source and installed plugin are distinct. An earlier installed App v1.0.6
observation with only three-tool guidance is retained as historical evidence,
not current installed state. Current release/version is host runtime evidence:
read it back when it matters, update the **existing** plugin through a guarded
release, and preserve the App binding. An already-open chat may still cache old
skill text. This repo's `scripts/verify_session_search_app_overlay.py` guards
the current five-tool guidance and nullable notes readback contract on every
canonical QA run; actual runtime state is checked separately, never inferred from Git.

## Verified runtime checkpoint — 2026-11-22 / issue #304

Fresh App-backed readback after the 2026-11-21 ChatGPT corpus promotion
confirmed that the stable plugin identity is serving the new ChatGPT generation
pair while preserving the same canonical Arcade worker and production router
URI.

Observed runtime evidence:

- chatgpt-0001 generation
  0f0153c6fbdbd747bfcef80779ebaae1327a8101d9bc3051711d038e301774c9;
- chatgpt-0002 generation
  5004e3c94709be89ed25e1fe51ada6e8bd691a493fecf86e13b0712378ed0e8a;
- catalog bounds complete;
- pre-watermark coverage:
  VERIFIED_TO_PROVIDER_REPORTED_WATERMARK;
- later coverage:
  STALE_AFTER_PROVIDER_REPORTED_WATERMARK;
- provider-export watermark:
  2026-11-19T18:43:36.744Z;
- strict ChatGPT and broad searches:
  FOUND with unavailable_shards=[];
- strict impossible control:
  NO_SIGNAL with unavailable_shards=[].

The rollout also established an operator-relevant cache rule: an env-only Neon
Function deployment can be control-plane completed while a long-lived router
still serves previously parsed module-scope configuration. A data-only catalog
refresh therefore requires a fresh runtime deployment of the existing accepted
router on the same production URI, followed by Arcade worker tool refresh and
fresh App-backed readback. The canonical operator sequence lives in
docs/federated-serving-promotion-runbook.md.

This is dated runtime evidence, not a promise that the same generation IDs or
watermark remain current forever. Source overlay and installed plugin are still
separate states; host release/current skill text must be read back independently.

## Verified Gemini production checkpoint — 2026-11-22 / issues #311 and #332

Fresh App-backed readback after the official Gemini promotion confirmed five READY shards in the same production identity: chatgpt-0001, chatgpt-0002, deepseek-0001, xai-0001, and gemini-0001. The canonical Arcade worker remains theseus-session-search on the original production router URI, and the plugin still exposes the same five consumer tools.

Gemini provider-scoped coverage reports VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT at/before the accepted Gemini Apps Activity Takeout request boundary and STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT beyond it. This is intentionally weaker than a provider-completeness watermark. Fresh Gemini strict search returned FOUND with exact gemini-0001 generation provenance and unavailable_shards=[]; a broad impossible control queried all five shards with unavailable_shards=[].

The same rollout re-confirmed that a control-plane completed router deployment is not sufficient evidence for module-scope config activation: the original URI showed five READY shards only after a distinct runtime-only redeploy, followed by Arcade worker tool refresh and App-backed readback.

This is dated runtime evidence. The live catalog remains runtime state rather than a permanent constant, and the Gemini snapshot contract does not claim complete lifetime Gemini history.

## Historical public-source / installed-App checkpoint — 2026-11-19

The source overlay contains two complementary agent skills:

- `skills/session-search-usage/SKILL.md` — bounded retrieval, grouped evidence and feedback boundary;
- `skills/session-browse-usage/SKILL.md` — pinned, read-only conversation paging and coverage.

The browse skill was copied exactly from the existing private App plugin release
**v1.0.5** into this public source. The existing `session-search-usage` and
gateway reference in Git already contain newer grouped-result and feedback
guidance than v1.0.5; **do not overwrite them blindly with older installed
copies**. Therefore this overlay is the reviewed desired source, not a claim
that every file currently matches installed App bytes. A future App skill
update needs a guarded release and independent host readback.

At this checkpoint, the existing Arcade Gateway / ChatGPT host discovered
`session_search`, `session_browse` and `session_feedback`. Neon router
`ssrouter2` deployment #12 has the agent-facing conditional translated-query
hint in `session_search` metadata. These are dated observations, not durable
claims about a future serving generation or automatic client hot reload.

## Stable consumer-tool identity

The production Arcade gateway is an allowlist of discovered tool definitions, not a transparent pass-through for every tool exposed by every registered remote server. Session Search therefore keeps one stable qualified tool identity across router deployments:

`TheseusSessionSearch.session_search`

The canonical remote-server identity remains theseus-session-search.
The existing public search tool remains stable (session_search), with
no version-suffixed sibling. Under issue #232 the same server can
additionally expose session_feedback for bounded App-only feedback
writes. This is conditional on an installed database writer and a
verified Arcade gateway allowlist and App-host tool listing; an
unverified source change is not proof the tool is live.

This indirection is the reason the App-backed ChatGPT plugin does not need a new domain-tool identity for router upgrades. Skill-overlay updates are only needed when operator/consumer guidance changes.

## Existing host maintenance

The production App-backed plugin already exists. Ordinary overlay maintenance must update that existing plugin identity rather than create a replacement.

Use Plugin Creator's App-backed update path:
- resolve the exact existing backend plugin ID;
- read the current release and retain its exact `current_release_id`;
- upload only the changed skill/support files;
- pass the guarded `expected_release_id`;
- preserve the existing App binding, MCP configuration, name, and presentation;
- read back the new release and run a fresh host smoke.

Do not recreate the App merely because a standalone remote-MCP package is easier to test on desktop.

## First-time host creation (historical/bootstrap only)

If a new environment genuinely has no App-backed Session Search plugin, create the web/mobile-compatible App binding in ChatGPT using the host custom-MCP flow:

1. Open Plugins.
2. Select +, then **Add custom MCP server**.
3. Use the canonical Arcade gateway URL documented in `skills/session-search-usage/references/gateway.md`.
4. Configure authentication as OAuth.
5. Accept the risk warning.
6. Select **Create as a plugin** and review the discovered Session Search capability.

After ChatGPT creates the App-backed plugin, use Plugin Creator only to layer this skill/support overlay onto that live release while preserving its existing `.app.json` binding and MCP configuration.

## Web/mobile compatibility invariant

Do not package the production plugin as a standalone remote-MCP plugin.

Specifically:
- do not add `mcp.json` or `.mcp.json`;
- do not commit the private `.app.json`;
- do not invent or predeclare an `asdk_app_...` ID;
- do not create a replacement plugin/App identity during an overlay refresh;
- do not replace the host-created App binding through an archive update.

Standalone remote-MCP packages are diagnostic/desktop-only for this project. Production web/mobile usage remains App-backed.

## Host acceptance

Host acceptance is runtime/catalog-aware, not tied to one historical shard pair.

A fresh ChatGPT runtime must:
1. use the existing App-backed Session Search plugin;
2. discover the Session Search domain tool through the Arcade host surface;
3. execute a bounded read-only query;
4. receive a contract-valid terminal status with explicit `queried_shards` and `unavailable_shards`;
5. preserve shard/generation provenance where returned.

`FOUND` and `NO_SIGNAL` are both healthy terminal query outcomes when all selected shards responded. `PARTIAL` proves the host path executed but means serving is degraded and must not be reported as fully healthy.

The historical #64 synthetic `chatgpt-0001 + hermes-0001` smoke remains point-in-time evidence only. Current acceptance uses the live shard catalog and current deployment evidence.
