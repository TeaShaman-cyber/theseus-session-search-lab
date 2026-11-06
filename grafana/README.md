# Session Search — Grafana dashboards as code

**Owner:** this repository. **Issue:** [#109](https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/109). Grafana Cloud is the shared observability runtime; the machine running `gcx` is not the owner of this service's configuration.

## Candidate dashboard (read-only Git capture)

`dashboards.v0alpha1.dashboard.grafana.app/session-search-candidate.json` is the original live nine-panel **Session Search — candidate canary** dashboard, captured through official `gcx resources pull dashboards/session-search-candidate` on 2026-11-01. The `spec` and UID (`metadata.name`) are unchanged. Only deployment-bound `metadata.namespace` and generated `metadata.annotations` were removed to avoid publishing account-specific metadata.

Panels 1–5: Prometheus/Mimir cumulative snapshots and provenance. Panels 6–9: Tempo/TraceQL window event counts and latency p50/p95. They are **not** interchangeable metrics, especially with low event counts and serverless resets.

**Important:** both Mimir and Tempo queries are intentionally filtered to the original `candidate` environment. Recent named integration canaries can therefore produce data while these panels appear empty. Changing filters, broadening environment scope, alerting, or applying this dashboard to live Grafana are separate reviewed changes under #109. Never silently mix candidate and production monitoring.

## First response: read-only observability for live Session Search

**Use this before rediscovering the Neon router/shard deployment graph.** Grafana Cloud is already instrumented for Session Search; start with the existing `session-search-candidate` dashboard and its Mimir/Tempo datasources. Its saved `candidate` filter may hide other live environments; on 2026-11-06 the observed router telemetry was labeled `issue137-candidate`. Discover the **current** labels instead of assuming that label remains valid.

1. **Mimir / Prometheus:** inspect `session_search_requests_total` and `session_search_shard_dispatches_total`. For sparse serverless samples, a bounded, read-only query such as `sum by (deployment_environment_name,service_version,provider,shard_id) (last_over_time(session_search_shard_dispatches_total[1h]))` is a *snapshot*, not a one-hour event count. An empty instant vector is not proof that ingestion is broken.
2. **Tempo:** search traces for `resource.service.name = "session-search-router"`, then inspect `session_search.shard_dispatch` spans. They expose `session_search.provider`, `session_search.shard_id`, `session_search.generation_id`, `resource.service.version` and `resource.deployment.environment.name` as read-only request evidence. On 2026-11-06, all four traced generation prefixes matched independently queried Neon `READY+ACTIVE` pointers; see [#166 checkpoint](https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/166#issuecomment-6682807064).
3. **Verification boundary:** telemetry shows **observed** router-to-shard behavior and generation provenance, but intentionally does **not** expose HTTPS endpoint URLs, auth secrets or a definitive copy of `SESSION_SEARCH_CATALOG`. Correlate it with the *currently registered* Arcade router, Neon Function deployment/branch identities and Neon SQL before claiming exact topology or rollback readiness. Do not derive live config from old issue snapshots.

This is a *diagnostic route*, not permission to change the Grafana filters, deploy Functions, promote a generation, or mutate the router catalog. The repository map produced in CI is derived and non-authoritative; these versioned docs are the operational entrypoint.

## Verify the captured resource (no live mutation)

The connected Grafana CLI 1.7.1 in MarcoPolo is installed and documented in [marcopolo-cookbook/grafana](https://github.com/TeaShaman-cyber/marcopolo-cookbook/tree/main/grafana). Use an authorized private OAuth profile; never commit or print it. With that approved local profile:

```sh
D=/workspace/.config/gcx-tealaphid71
GCX_KEYCHAIN=off /workspace/tools/gcx/bin/gcx --context tealaphid71 \
  resources validate dashboards/session-search-candidate -p ./grafana --config "$D/config.yaml"
GCX_KEYCHAIN=off /workspace/tools/gcx/bin/gcx --context tealaphid71 \
  resources push dashboards/session-search-candidate --dry-run -p ./grafana --config "$D/config.yaml"
python3 -m unittest discover -s tests -p test_grafana_dashboard_contract.py
```

These are verification operations, **not** permission to run a real `resources push`. A live change requires an explicitly reviewed diff, normal repository change control, and post-deployment dashboard readback. Before any future refresh, inspect the live dashboard and compare its `spec` to this file; Grafana UI edits must not silently overwrite Git (or vice versa).

Acceptance snapshot 2026-11-01: server validation passed, dry-run passed, all nine panels and both datasource families preserved, resource spec equality verified against the direct live export. No dashboard mutation or alert creation was performed.
