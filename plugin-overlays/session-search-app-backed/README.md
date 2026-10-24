# Session Search App-backed overlay

This directory is the public, reproducible skill overlay for the private App-backed Session Search ChatGPT plugin created from the canonical Arcade gateway.

Desired production path:

```text
ChatGPT web/mobile
  -> App-backed Session Search plugin
  -> Arcade Session Search gateway
  -> TheseusSessionSearch.session_search
  -> ssrouter2
  -> federated Neon projection shards
```

The ChatGPT App binding itself is private host state and is deliberately not stored in this repository.

## Host creation

Create the web-compatible App binding in ChatGPT using the current custom-MCP flow:

1. Open Plugins.
2. Select +, then **Add custom MCP server**.
3. Use the canonical Arcade gateway URL documented in `skills/session-search-usage/references/gateway.md`.
4. Configure authentication as OAuth.
5. Accept the risk warning.
6. Select **Create as a plugin** and review the discovered tool.

After ChatGPT creates the App-backed plugin, use Plugin Creator only to layer this skill/support overlay onto the live release while preserving its existing `.app.json` binding and MCP configuration.

## Web/mobile compatibility invariant

Do not package the production plugin as a standalone remote-MCP plugin.

Specifically:
- do not add `mcp.json` or `.mcp.json`;
- do not commit the private `.app.json`;
- do not invent or predeclare an `asdk_app_...` ID;
- do not replace the host-created App binding through an archive update.

Standalone remote-MCP packages are diagnostic/desktop-only for this project. Production web/mobile usage remains App-backed.
