# Canonical Session Search gateway

Current issue #64 synthetic acceptance target:

- Arcade remote server: `theseus-session-search`
- Arcade gateway: `Session Search`
- Gateway ID / slug: `gw_3L9SqeBQ5L5Cs2V1GB6yvnKhR8P`
- MCP URL: `https://api.arcade.dev/mcp/gw_3L9SqeBQ5L5Cs2V1GB6yvnKhR8P`
- Authentication for ChatGPT custom-MCP creation: OAuth
- Expected exposed tool count: 1
- Expected tool: `TheseusSessionSearch.session_search`

Canonical remote MCP behind Arcade is the independently deployed Session Search router. The consumer App should connect to the Arcade gateway URL above, not directly to an individual Neon shard and not to a portable `mcp.json` package.

## Acceptance

Gateway/control-plane existence is not host acceptance.

Final host acceptance requires a fresh ChatGPT runtime using the host-created App-backed plugin and a successful `session_search` call returning the expected synthetic two-shard result.

## Current promotion boundary

The endpoint is suitable for synthetic acceptance only. Real-corpus promotion remains blocked until current production edge authentication and cutover policy are explicitly verified.
