# Arcade bundle consumer and lifecycle materializer

## Purpose

The Arcade Hosted path removes MarcoPolo from steady-state corpus-byte transport while preserving source authority and explicit promotion boundaries.

```text
accepted corpus authority
        -> deterministic bundle.v1
        -> private Google Drive
        -> Arcade inspect/verify
        -> Arcade BUILDING-only Neon load
        -> [separate boundary] READY / activation / catalog
```

Google Drive is transport, not authority. The mandatory external bundle SHA-256 and the bundle's manifest/digests bind the bytes to accepted source evidence.

## Accepted read-only slice

`inspect_bundle(drive_file_id, expected_bundle_sha256)` uses Google OAuth `drive.readonly`, streams the exact Drive object to ephemeral storage, enforces the bundle-size bound, checks the external SHA-256, and independently verifies the full bundle provenance chain. Returned data is aggregate only.

The real xAI bundle completed Hosted end-to-end verification on 2026-10-24: 60 sessions, 17,790 searchable messages, generation `6095e643b3fc4f133c94c04db24565dce6bbd456a308860820e94e70f8047835`, with the server log independently showing Google Drive HTTP 200 and tool success.

The reusable operator path is `.github/workflows/smoke-session-search-arcade-inspector.yml`, backed by `scripts/arcade_inspect_bundle.py`. It requires an exact Drive file ID plus an expected external bundle SHA-256, discovers exactly one Hosted `InspectBundle` tool with toolkit-scoped lookup, requires Google authorization to already be completed, and validates the returned `VERIFIED`/schema/bundle-digest postcondition. The workflow has no Neon, READY, activation, router, or catalog inputs.

## BUILDING-only slice

`materialize_bundle(...)` reuses the same single verification pass and then resolves a Neon connection URI through Neon control plane using Arcade-managed `NEON_API_KEY`. The API key must be scoped to the one target project.

The SQL login role is fixed to `session_search_arcade_loader_sql`; it is not caller-selectable. At transaction start the loader executes fixed `SET LOCAL ROLE session_search_building_loader`. That capability is supplied by `sql/neon/004_arcade_building_loader.sql`, which grants only schema usage plus SELECT/INSERT on the three projection tables. It has no UPDATE/DELETE, `mark_generation_ready`, `activate_generation`, or catalog capability.

Materialization is transaction-bound and retry-idempotent:

- new exact generation -> `LOADED`, state remains `BUILDING`;
- exact existing BUILDING -> `ALREADY_BUILDING`, no reinsertion;
- exact existing READY -> `ALREADY_READY`, no mutation;
- identity/count/state mismatch -> fail closed.

The public tool contract never accepts or returns `DATABASE_URL`; the connection URI is resolved at invocation time and retained only in process memory. OAuth tokens, Neon API keys, connection URIs, and private message text are never returned.

The reusable BUILDING operator is `.github/workflows/materialize-session-search-building.yml`, backed by `scripts/arcade_materialize_bundle.py`. It accepts only exact Drive bundle identity plus one Neon project/branch/database target, uses the stable repository Arcade user identity, discovers exactly one Hosted `MaterializeBundle` tool, and validates returned bundle/target/loader identity plus the documented idempotent status. It exposes no READY, activation, router, or catalog inputs.

## READY-only slice

`mark_bundle_ready(...)` is intentionally separate from `materialize_bundle(...)`. It accepts only the exact target identity plus already verified generation/manifest/count evidence and resolves a Neon connection using the fixed SQL login `session_search_arcade_ready_sql`.

At transaction start it executes fixed `SET LOCAL ROLE session_search_ready_marker`. The versioned `sql/neon/005_ready_marker_capability.sql` grants that capability only schema usage plus EXECUTE on `mark_generation_ready(...)`. The database function independently requires the generation to still be BUILDING, checks the source-manifest digest and exact session/message counts, and only then writes READY/`ready_at`.

The READY tool has no table DML privilege and no `activate_generation` capability. Activation, router/catalog updates, and production cutover remain separate protected operations.

## Deployment boundary

Arcade Hosted deployment is manual-only through `.github/workflows/deploy-session-search-arcade-materializer.yml`. The currently deployed `0.2.0` BUILDING-capable server remains distinct from the `0.3.0` READY-capable code until an explicit redeploy. The same Arcade-managed project-scoped Neon secret is used only to resolve fixed-role connection URIs.

Generation activation, router/catalog updates, and production cutover remain separate protected operations; READY is exposed only through the dedicated least-privilege tool above.

The versioned operator path for this READY boundary is `.github/workflows/promote-session-search-ready.yml`, which calls `scripts/arcade_mark_bundle_ready.py` with exact project/branch/generation/manifest/count inputs. The entrypoint discovers exactly one Hosted `MarkBundleReady` tool, fails closed on ambiguity or unexpected interactive authorization, and validates the returned READY postcondition. It has no activation or catalog parameters. Independent Neon readback remains the acceptance check after a successful run.

## Activation-only slice

`activate_ready_generation(...)` is a fourth, separately privileged Hosted tool. It resolves Neon using the fixed login `session_search_arcade_activator_sql`, then executes fixed `SET LOCAL ROLE session_search_activation_marker`. The versioned `sql/neon/006_activation_marker_capability.sql` grants only schema usage plus EXECUTE on `activate_generation(text,text)`. It cannot materialize, mark READY, or mutate projection tables directly.

Activation is compare-and-set: callers must provide the exact currently active generation ID, or literal `NULL` when the pointer is empty. The database function locks the singleton active-pointer row, rejects stale expectations, requires the candidate generation to be READY, and only then changes `active_generation`.

The versioned operator path is `.github/workflows/activate-session-search-generation.yml`, backed by `scripts/arcade_activate_generation.py`. It has no router, external serving catalog, publication, or production-cutover parameter. Independent Neon readback of the active pointer remains the acceptance check.

## Private verified session selection for source handoff (#59)

The existing independent streaming verifier can collect only requested
session IDs while verifying the exact immutable bundle:
`verify_bundle_session_selection(path, expected_bundle_sha256=..., session_ids=...)`.
It returns the unchanged aggregate verification receipt and a separate sorted
tuple of matched IDs. Both are returned only after the existing full
verification succeeds. It retains O(requested IDs) selection state and does
not collect or reread message rows. Unlike the all-rows compatibility
helper, this uses the existing streaming path without a second CSV parse.
Existing member digest and outer SHA passes are unchanged.

This is an internal Python library operation for trusted source/refresh
execution, not a new Arcade tool or public output. The caller must supply
the expected digest from independently verified promotion custody and
maintain immutable file custody during verification. An unmatched ID is
only absent from this one verified bundle; not proof of corpus absence or
scope completeness. The caller must check provider, accepted-set identity,
exact generation, complete catalog coverage and unique placement across
the relevant bundle set. No production deploy, permission changes or
private-history egress are implied.
