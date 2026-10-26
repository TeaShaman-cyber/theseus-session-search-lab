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
