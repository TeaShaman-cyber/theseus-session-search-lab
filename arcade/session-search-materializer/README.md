# Session Search Arcade materializer

This package is the Arcade Hosted consumer for `theseus.session-search-shard-bundle.v1`.

Current surface has four deliberately separate tools:

- `inspect_bundle(...)` — Drive `read-only` download plus independent bundle verification;
- `materialize_bundle(...)` — the same verification followed by a BUILDING-only load into Neon;
- `mark_bundle_ready(...)` — a READY-only lifecycle transition for one already verified BUILDING generation;
- `activate_ready_generation(...)` — a CAS-style active-pointer transition for one READY generation.

`inspect_bundle` requires Google OAuth `drive.readonly` and returns aggregate evidence only.

`materialize_bundle` additionally requires the Arcade-managed secret `NEON_API_KEY`. That key must be project-scoped to the target Neon project. The tool accepts target project/branch identity, but the database role is fixed in code to `session_search_arcade_loader_sql`; callers cannot select a privileged role. Neon control plane resolves the connection URI at invocation time, so `DATABASE_URL` is not part of the tool contract or persistent server configuration.

The BUILDING login receives SET-only membership in the versioned `session_search_building_loader` capability from `sql/neon/004_arcade_building_loader.sql`: schema usage plus SELECT/INSERT on `generations`, `sessions`, and `messages`. It has no lifecycle function execution, table UPDATE/DELETE, READY transition, activation, or catalog rights.

The READY login is separately fixed to `session_search_arcade_ready_sql`. It receives SET-only membership in `session_search_ready_marker` from `sql/neon/005_ready_marker_capability.sql`, which grants only schema usage plus EXECUTE on `mark_generation_ready(...)`. It has no table DML and no `activate_generation` capability.

The activation login is separately fixed to `session_search_arcade_activator_sql`. It receives SET-only membership in `session_search_activation_marker` from `sql/neon/006_activation_marker_capability.sql`, which grants only schema usage plus EXECUTE on `activate_generation(...)`. The database function requires the caller's exact expected previous active generation and only accepts a READY candidate, making activation a compare-and-set boundary. It has no table DML or READY-marking capability.

Exact replay is idempotent: an identical existing BUILDING generation returns `ALREADY_BUILDING`, identical READY returns `ALREADY_READY`, and any identity/count mismatch fails closed. A fresh successful load returns `LOADED` with target state `BUILDING`.

## Deployment

The GitHub deployment workflow remains manual-only. `ARCADE_URL` and `ARCADE_API_KEY` authenticate deployment; `NEON_API_KEY` belongs in Arcade's secret store for the Hosted server and is never returned by the tool.

Activation is an explicit separate tool/capability and never occurs during materialization or READY marking. Router/catalog mutation and production cutover are not part of this package surface.
