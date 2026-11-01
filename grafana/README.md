# Session Search — Grafana dashboards as code

**Owner:** this repository. **Issue:** [#109](https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/109). Grafana Cloud is the shared observability runtime; the machine running `gcx` is not the owner of this service's configuration.

## Candidate dashboard (read-only Git capture)

`dashboards.v0alpha1.dashboard.grafana.app/session-search-candidate.json` is the original live nine-panel **Session Search — candidate canary** dashboard, captured through official `gcx resources pull dashboards/session-search-candidate` on 2026-11-01. The `spec` and UID (`metadata.name`) are unchanged. Only deployment-bound `metadata.namespace` and generated `metadata.annotations` were removed to avoid publishing account-specific metadata.

Panels 1–5: Prometheus/Mimir cumulative snapshots and provenance. Panels 6–9: Tempo/TraceQL window event counts and latency p50/p95. They are **not** interchangeable metrics, especially with low event counts and serverless resets.

**Important:** both Mimir and Tempo queries are intentionally filtered to the original `candidate` environment. Recent named integration canaries can therefore produce data while these panels appear empty. Changing filters, broadening environment scope, alerting, or applying this dashboard to live Grafana are separate reviewed changes under #109. Never silently mix candidate and production monitoring.

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
