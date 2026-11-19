#!/usr/bin/env python3
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "plugin-overlays" / "session-search-app-backed"
README = OVERLAY / "README.md"
SKILL = OVERLAY / "skills" / "session-search-usage" / "SKILL.md"
GATEWAY = OVERLAY / "skills" / "session-search-usage" / "references" / "gateway.md"
SERVING = ROOT / "docs" / "federated-serving-plane.md"
BROWSE = OVERLAY / "skills" / "session-browse-usage" / "SKILL.md"
FEEDBACK_DOC = ROOT / "docs" / "feedback-sync.md"
FEEDBACK_CODE = ROOT / "functions" / "router" / "feedback.mjs"
FEEDBACK_CORE = ROOT / "functions" / "router" / "core.mjs"

for path in (README, SKILL, GATEWAY, SERVING, BROWSE, FEEDBACK_DOC, FEEDBACK_CODE, FEEDBACK_CORE):
    if not path.is_file():
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL missing {path.relative_to(ROOT)}")

readme = README.read_text(encoding="utf-8")
skill = SKILL.read_text(encoding="utf-8")
gateway = GATEWAY.read_text(encoding="utf-8")
serving = SERVING.read_text(encoding="utf-8")
browse = BROWSE.read_text(encoding="utf-8")
feedback_doc = FEEDBACK_DOC.read_text(encoding="utf-8")
feedback_code = FEEDBACK_CODE.read_text(encoding="utf-8")
feedback_core = FEEDBACK_CORE.read_text(encoding="utf-8")

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
    "strict_group_count",
    "occurrences[]",
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
    "strict_group_count",
    "result groups",
    "TheseusSessionSearch.session_feedback",
    "two intentional consumer tools",
    "PATCH /v1/workers/{id}",
    "POST /v1/workers/{id}/tools/refresh",
    "stable qualified tool identity",
):
    if marker not in gateway:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL gateway marker: {marker}")


for marker in (
    "TheseusSessionSearch.session_browse",
    "next_cursor",
    "PARTIAL_SESSION_SLICE",
    "coverage_complete",
    "Arcade_SelectTools",
):
    if marker not in browse:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL BROWSE marker: {marker}")

# Keep the source docs/skills and router feedback contract synchronized.
# Dated historical gateway checkpoints are fine; do not silently drop the
# private read-by-ref tool or nullable notes visibility from current guidance.
for label, document in (
    ("README", readme),
    ("search skill", skill),
    ("browse skill", browse),
    ("gateway", gateway),
    ("feedback sync", feedback_doc),
):
    if "session_feedback_get" not in document:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL {label} feedback readback missing")
for label, document in (
    ("README", readme),
    ("search skill", skill),
    ("gateway", gateway),
    ("feedback sync", feedback_doc),
):
    if "notes_present" not in document or "`notes`" not in document:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL {label} nullable notes contract missing")
if "all three tools:" in skill or "The production gateway exposes three verified consumer tools" in gateway:
    raise SystemExit("SESSION_SEARCH_APP_OVERLAY_FAIL stale undated three-tool claim")
if "name: 'session_feedback_get'" not in feedback_core or "notes: feedback.notes" not in feedback_code:
    raise SystemExit("SESSION_SEARCH_APP_OVERLAY_FAIL router/readback notes contract mismatch")

if "still selects only session_search when asked for feedback" in skill:
    raise SystemExit("SESSION_SEARCH_APP_OVERLAY_FAIL stale current feedback discovery assertion")

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

for marker in (
    "two intentional consumer tools",
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

print("SESSION_SEARCH_APP_OVERLAY_PASS")
