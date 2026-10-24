#!/usr/bin/env python3
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "plugin-overlays" / "session-search-app-backed"
README = OVERLAY / "README.md"
SKILL = OVERLAY / "skills" / "session-search-usage" / "SKILL.md"
GATEWAY = OVERLAY / "skills" / "session-search-usage" / "references" / "gateway.md"

for path in (README, SKILL, GATEWAY):
    if not path.is_file():
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL missing {path.relative_to(ROOT)}")

readme = README.read_text(encoding="utf-8")
skill = SKILL.read_text(encoding="utf-8")
gateway = GATEWAY.read_text(encoding="utf-8")

for marker in (
    "App-backed",
    "do not add `mcp.json` or `.mcp.json`",
    "Create as a plugin",
):
    if marker not in readme:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL README marker: {marker}")

for marker in (
    "TheseusSessionSearch.session_search",
    "Do **not** add `mcp.json` or `.mcp.json`",
    "PARTIAL",
    "copper compass",
):
    if marker not in skill:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL SKILL marker: {marker}")

for marker in (
    "gw_3L9SqeBQ5L5Cs2V1GB6yvnKhR8P",
    "https://api.arcade.dev/mcp/gw_3L9SqeBQ5L5Cs2V1GB6yvnKhR8P",
    "TheseusSessionSearch.session_search",
):
    if marker not in gateway:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL gateway marker: {marker}")

for forbidden in ("mcp.json", ".mcp.json", ".app.json"):
    for path in OVERLAY.rglob(forbidden):
        raise SystemExit(
            f"SESSION_SEARCH_APP_OVERLAY_FAIL forbidden packaged binding: {path.relative_to(ROOT)}"
        )

print("SESSION_SEARCH_APP_OVERLAY_PASS")
