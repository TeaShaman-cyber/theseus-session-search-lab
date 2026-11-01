#!/usr/bin/env python3
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "plugin-overlays" / "session-search-app-backed"
README = OVERLAY / "README.md"
SKILL = OVERLAY / "skills" / "session-search-usage" / "SKILL.md"
GATEWAY = OVERLAY / "skills" / "session-search-usage" / "references" / "gateway.md"
SERVING = ROOT / "docs" / "federated-serving-plane.md"

for path in (README, SKILL, GATEWAY, SERVING):
    if not path.is_file():
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL missing {path.relative_to(ROOT)}")

readme = README.read_text(encoding="utf-8")
skill = SKILL.read_text(encoding="utf-8")
gateway = GATEWAY.read_text(encoding="utf-8")
serving = SERVING.read_text(encoding="utf-8")

for marker in (
    "App-backed",
    "do not add `mcp.json` or `.mcp.json`",
    "Create as a plugin",
    "stable qualified tool identity",
    "gateway is an allowlist",
):
    if marker not in readme:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL README marker: {marker}")

for marker in (
    "TheseusSessionSearch.session_search",
    "Do **not** add `mcp.json` or `.mcp.json`",
    "PARTIAL",
    "copper compass",
    "Arcade_SelectTools",
    "queried_shards",
    "unavailable_shards",
    "live shard catalog",
    "historical #64",
    "search_mode",
    "strict",
    "recall",
    "auto",
    "requested_mode",
    "effective_mode",
    "strict_hit_count",
    "stable qualified tool identity",
):
    if marker not in skill:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL SKILL marker: {marker}")

for marker in (
    "gw_3L9SqeBQ5L5Cs2V1GB6yvnKhR8P",
    "https://api.arcade.dev/mcp/gw_3L9SqeBQ5L5Cs2V1GB6yvnKhR8P",
    "TheseusSessionSearch.session_search",
    "historical #64",
    "live shard catalog",
    "search_mode",
    "auto",
    "retrieval",
    "one consumer tool",
    "PATCH /v1/workers/{id}",
    "POST /v1/workers/{id}/tools/refresh",
    "stable qualified tool identity",
):
    if marker not in gateway:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL gateway marker: {marker}")


legacy_current_smoke = "require `FOUND` with both `chatgpt-0001` and `hermes-0001` provenance"
if legacy_current_smoke in skill:
    raise SystemExit(
        "SESSION_SEARCH_APP_OVERLAY_FAIL stale #64 synthetic smoke is still current acceptance"
    )

for forbidden in ("mcp.json", ".mcp.json", ".app.json"):
    for path in OVERLAY.rglob(forbidden):
        raise SystemExit(
            f"SESSION_SEARCH_APP_OVERLAY_FAIL forbidden packaged binding: {path.relative_to(ROOT)}"
        )

print("SESSION_SEARCH_APP_OVERLAY_PASS")

for marker in (
    "one consumer tool",
    "stable qualified tool identity",
    "PATCH /v1/workers/{id}",
    "POST /v1/workers/{id}/tools/refresh",
):
    if marker not in serving:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL serving marker: {marker}")

for document_name, document in (("README", readme), ("SKILL", skill), ("gateway", gateway), ("serving", serving)):
    if "session_search_v2" in document:
        raise SystemExit(
            f"SESSION_SEARCH_APP_OVERLAY_FAIL {document_name} must not promote a second production tool identity"
        )
