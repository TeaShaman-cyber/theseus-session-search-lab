# Federated serving promotion runbook

## Purpose

This is the canonical operator path for a data-only Session Search refresh:
new accepted corpus evidence should become live through the existing federated
Neon router and the existing App-backed Session Search identity.

The simple model is:

    accepted source evidence
      -> deterministic shard bundle
      -> fresh green Neon DATA project
      -> BUILDING -> READY generation
      -> existing reviewed shard FUNCTION host
           -> only its least-privilege database binding changes
      -> isolated router candidate
      -> existing production router identity / URI
      -> existing Arcade worker + tool refresh
      -> existing ChatGPT App

Keep those layers separate. A green data project is not automatically a
Function host, and a successful Neon control-plane deployment is not host
acceptance.

This runbook does not authorize a router/shard code release, auth rotation,
new provider topology, new App/server/tool identity, source-authority move, or
rollback deletion. Those are separate changes with their own Issue and
acceptance.

## Why this runbook exists

Issue #304 promoted the 2026-11-21 ChatGPT refresh successfully, but it exposed
three recurring sources of operator error:

1. Data and serving were conflated. The deterministic bundle/materializer path
   was correct, but documentation made it too easy to assume that each fresh
   green data project also had to receive a fresh copy of shard Function code
   and auth.
2. Environment update was mistaken for runtime reload. A Neon Function
   deployment can report completed/active while a long-lived module keeps a
   previously parsed module-scope catalog. The production router required a
   fresh runtime deployment before the new catalog bounds were actually served.
3. Control-plane success was mistaken for App freshness. Arcade worker tool
   discovery also needed an explicit refresh before the App-backed host exposed
   the new coverage state.

The resulting rule is deliberately boring:

    data verify
      -> BUILDING
      -> READY
      -> candidate serving endpoints
      -> candidate router acceptance
      -> production config update
      -> runtime redeploy
      -> Arcade refresh
      -> App-backed readback

Do not skip layers or infer one layer from another.

## Layer ownership

### Source / data authority

Accepted artifacts, ledger state, accepted-set identity, source manifest, and
provider export watermark are authority. Neon is a rebuildable projection.

For provider-aware replacement shards, use the existing bounded source gate and
bundle contract:

- scripts/verify_shard_source.py;
- scripts/shard_bundle.py;
- [shard bundle transport](shard-bundle-transport.md).

A bundle is private transport data, not public repository content.

### Green data projects

Separate-project blue/green on the Neon Free plan refers to data projects. A
replacement generation belongs in a fresh target project so a near-full
production project is not asked to hold a second full generation.

The normal data path is already source-controlled:

1. privately stage the exact deterministic bundle in Google Drive;
2. run .github/workflows/smoke-session-search-arcade-inspector.yml;
3. run .github/workflows/materialize-session-search-building.yml;
4. independently read back the exact BUILDING generation/count/provenance;
5. run .github/workflows/promote-session-search-ready.yml;
6. independently read back READY.

The workflows are backed by the fixed-role Arcade materializer scripts described
in [Arcade bundle consumer](arcade-bundle-consumer.md). Do not replace this with
model-visible bundle bytes, ad-hoc direct SQL loading, or the historical direct
database-URL materializer merely because an operator shell is available.

ACTIVE is a compatibility/rollback surface. The production router pins exact
catalog generation IDs and must not infer serving authority from ACTIVE alone.

### Shard Function hosts

For a data-only refresh, do not upload shard Function code again merely because
the data project is new.

The shard Function is a serving adapter. It needs:

- the already reviewed Function source/runtime;
- the existing shared SESSION_SEARCH_SHARD_AUTH_TOKEN;
- a least-privilege session_search_serving database URL for the selected data
  project.

The current safe pattern is to create a candidate serving branch from the
already accepted live shard Function host so Function source and secret-backed
environment are inherited, then change only SESSION_SEARCH_DATABASE_URL to the
new green data project.

This is an explicit separation between Function host and data project. Do not
infer hosting from a project name.

Current old-project Function hosting is transitional operational state, not a
recommendation to keep coupling future serving infrastructure to historical data
projects forever. A dedicated Function-host migration is a separate architecture
change; do not smuggle it into a routine corpus refresh.

If a candidate cannot inherit the reviewed Function/auth environment safely,
stop. Do not extract the shared bearer into chat, logs, Git, ad-hoc plaintext
files, or a new model-visible secret store just to make the refresh proceed.

## Preflight

Before any production mutation:

1. Re-read the covering Issue and exact accepted-source receipt.
2. Use a clean checkout at the exact intended Git head.
3. Run ./tools/dev/check for any source/doc change that will accompany the
   rollout.
4. Read back the current App-backed Session Search surface:
   - current ChatGPT generations;
   - current provider watermark/currentness;
   - current queried/unavailable shards.
5. Read back the production router identity/URI and current environment key
   names. Do not print secret values.
6. Record the current catalog/generation vector and old blue data/serving state
   as rollback.
7. Confirm the replacement generation IDs, session/message counts, source
   manifest, time bounds, and accepted-set identity from the verified green
   data projects.
8. Confirm DeepSeek/xAI or any other unchanged catalog members from current
   runtime evidence rather than a dated document.

A dated deployment number, resource name, or housekeeping inventory is evidence,
not the procedure.

## Bounded pre-cutover readiness receipt

Before opening live candidate/cutover gates, collapse deterministic repository
mechanics into one runtime job:

    tools/dev/serving-promotion-readiness \
      --manifest /private/path/promotion-readiness.json \
      --run-qa \
      --output /private/path/promotion-readiness-receipt.json

This command is deliberately **read-only** with respect to Session Search
providers. It does not call Neon, Arcade, Drive, GitHub mutation APIs, the App,
or the router. It:

- pins the exact Git HEAD / branch and requires a clean worktree;
- runs canonical ./tools/dev/check once when --run-qa is supplied;
- hashes the complete QA stdout/stderr instead of copying it into the receipt;
- validates target generation identity from source-manifest identity;
- validates READY state, aggregate counts, finite observed bounds, accepted-set
  consistency and provider watermark coverage;
- preserves explicit previous generation IDs as rollback metadata;
- emits a compact terminal READY_FOR_RUNTIME_GATES or BLOCKED receipt plus
  the remaining independent runtime gates.

The input is a versioned public-safe metadata manifest. It must not contain
database URLs, bearer/token/secret fields, Authorization material, private Drive
IDs, raw transcript text or other credential-bearing values. Unknown or
secret-like fields fail closed.

READY_FOR_RUNTIME_GATES does **not** authorize or prove promotion. It means the
deterministic/local layer is ready for the independently observed candidate
shard, candidate router, same-URI runtime reload, Arcade refresh and App-backed
acceptance gates described below.

Keep the manifest/receipt outside the repository when it describes a private
operational refresh. The command's JSON receipt is intended to replace repeated
chat-mediated git-status / QA / receipt-parsing / generation-comparison
round-trips with one bounded execution result.

## Candidate shard acceptance

For each replacement shard serving endpoint:

1. create the candidate serving branch from the accepted live Function host;
2. keep inherited Function source and shard-auth environment unchanged;
3. bind only the least-privilege green database URL;
4. require GET /health -> 200;
5. require unauthenticated POST /search -> 401;
6. require an authenticated, generation-pinned positive control;
7. require an authenticated negative control;
8. verify returned shard/generation provenance exactly.

Prefer an operator surface that can inject the existing bearer without exposing
it. If no such surface is available, a disposable issue-tracked sanitized probe
that inherits auth may be used only as a fallback. It must return metadata only,
must not expose credentials or transcript text, and must be removed/read back
before completion.

## Candidate router acceptance

Create the non-production router candidate from the current production router
branch so code, router auth, shard auth, telemetry, PostHog, and feedback
configuration are inherited.

Change only the candidate values needed for the refresh:

- SESSION_SEARCH_CATALOG;
- SESSION_SEARCH_SOURCE_WATERMARKS;
- SESSION_SEARCH_SOURCE_SNAPSHOTS;
- a non-secret candidate environment/service label when useful.

Every catalog entry must include the exact READY generation and the verified
finite time_min / time_max used by coverage. Omitting bounds degrades
session_coverage to UNKNOWN_COVERAGE even when search itself works.

Candidate acceptance requires:

- positive strict control -> FOUND;
- negative strict control -> NO_SIGNAL;
- exact expected generation provenance;
- all intended shards queried;
- unavailable_shards=[];
- both provider-scoped and broad fan-out where relevant.

Do not add a version-suffixed sibling Session Search tool to the production
gateway for this test.

## Production cutover

A routine data-only cutover preserves:

- production router project/branch/Function identity;
- production router URI;
- Arcade worker ID theseus-session-search;
- App/plugin identity and OAuth binding;
- router/shard auth;
- telemetry, PostHog, and feedback environment;
- unchanged provider shard endpoints/generations.

The normal production mutation is only:

1. update the exact SESSION_SEARCH_CATALOG;
2. update the exact SESSION_SEARCH_SOURCE_WATERMARKS;
3. verify all non-catalog environment key names remain present.

### Mandatory runtime reload

Do not treat an env-only Function deployment marked completed/active as proof
that module-scope configuration was reloaded.

The router parses catalog/currentness configuration into module-scope runtime
state. After a catalog/watermark environment update, force a fresh runtime
deployment of the existing accepted Function bundle/runtime on the same
production URI.

For a data-only refresh this is a runtime reload, not a code upload.

After that reload, directly verify protected production coverage/search metadata
where possible. The expected catalog generation IDs and time bounds must be
visible before blaming Arcade or the App.

## Arcade refresh

Keep the canonical remote server identity theseus-session-search.

If the production URI did not change, do not PATCH it merely for ceremony.
Regardless, after a backend runtime/config refresh call the supported worker tool
refresh:

    POST /v1/workers/theseus-session-search/tools/refresh

Use the existing authenticated Arcade operator surface. Do not delete/re-add the
server, create a replacement production worker, or create a second production
tool identity to defeat cache state.

If a candidate URI was temporarily used on an authorized operator path, restore
the original production URI before terminal acceptance and read it back.

## Terminal App-backed acceptance

Control-plane state is not enough. Use a fresh App-backed discovery path.

Require current discovery of the intended Session Search tools, including:

- TheseusSessionSearch.session_search;
- TheseusSessionSearch.session_browse;
- TheseusSessionSearch.session_coverage;
- TheseusSessionSearch.session_feedback;
- TheseusSessionSearch.session_feedback_get.

Then verify the source-currentness contract appropriate to the scoped provider:

1. for a provider-reporting export such as the accepted ChatGPT source, a window ending at or before the accepted provider watermark must return status=OBSERVED, catalog_bounds_complete=true, expected generation IDs and finite bounds, and source_currentness=VERIFIED_TO_PROVIDER_REPORTED_WATERMARK; a later/open window must return STALE_AFTER_PROVIDER_REPORTED_WATERMARK;
2. for a bounded provider export snapshot that does not claim provider completeness, currently Gemini Apps Activity, a window ending at or before source_snapshot.observed_through must return VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT with the expected source_scope/basis; a later/open window must return STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT;
3. if neither one coherent watermark nor one coherent snapshot binding is independently verified for every eligible shard, require fail-closed UNKNOWN_WITHOUT_SOURCE_WATERMARK rather than inventing freshness;
4. a bounded strict positive search:
   - terminal FOUND;
   - expected new generation provenance;
   - unavailable_shards=[];
5. a provider-scoped positive search where useful;
6. a bounded impossible strict control:
   - NO_SIGNAL;
   - unavailable_shards=[].

The provider watermark is the source snapshot boundary, not the current wall
clock. A deployment can be fully current for the latest accepted export and
still correctly report newer conversations as stale/outside observed range.

## Rollback

Keep the previous blue data projects/generations and previous serving branches
until terminal host acceptance and the covering Issue explicitly discharges the
rollback obligation.

Rollback is:

1. restore the previous catalog and watermark binding;
2. force the same production router runtime reload;
3. refresh the existing Arcade worker tools;
4. run fresh App-backed coverage/search acceptance.

Do not rebuild corpus data during emergency rollback unless the retained rollback
assets themselves are unavailable.

## Failure localization

Use the smallest failing boundary:

| Symptom | First boundary to inspect |
| --- | --- |
| bundle/workflow mismatch | accepted evidence, bundle verify, materializer receipt |
| BUILDING never becomes READY | READY workflow / fixed-role Neon lifecycle |
| shard health works but authenticated query fails | Function auth / DB serving binding |
| candidate router reports unavailable shard | exact endpoint/generation/auth for that shard |
| search works but coverage is UNKNOWN_COVERAGE | missing/invalid catalog time_min/time_max |
| Neon env deploy says completed but old catalog is served | Function runtime/module-scope cache; force runtime redeploy |
| direct production HTTP is correct but App is stale | Arcade worker tool refresh / host discovery |
| Arcade control plane is correct but host schema is old | fresh ChatGPT App discovery/reconnect before more mutation |

Do not skip directly from one symptom to a new provider, new worker identity, or
secret rotation.

## Forbidden normal-path shortcuts

For a routine data-only refresh, do not:

- send shard ZIP/bundle bytes through model-visible base64 or chat tool payloads;
- use direct raw SQL materialization in place of the Arcade bundle lifecycle;
- upload Function code when the reviewed Function source did not change;
- extract or rotate shared auth merely to make a green project self-contained;
- infer Function hosting from the green data-project identity;
- accept completed deployment status without runtime/host readback;
- replace the canonical Arcade worker/App identity to clear cache;
- delete old blue data/serving state before rollback disposition.

## Completion receipt

The covering Issue should record only public-safe evidence:

- exact Git source head;
- accepted-set/source-manifest identities and aggregate counts;
- new data generation IDs and aggregate time bounds;
- candidate fan-out acceptance and unavailable count;
- production router identity/URI preservation;
- runtime redeploy/readback;
- Arcade worker identity/refresh readback;
- App-backed coverage/currentness/search acceptance;
- rollback assets retained;
- disposable probe cleanup.

Never publish corpus text, private Drive IDs, database URLs, bearer values, or
secret material.
