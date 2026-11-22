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
ROOT_README = ROOT / "README.md"
RUNBOOK = ROOT / "docs" / "federated-serving-promotion-runbook.md"
GEMINI_SPEC = ROOT / "docs" / "gemini-official-corpus-serving-spec.md"
HOUSEKEEPING = ROOT / "docs" / "neon-housekeeping.md"

for path in (
    README,
    SKILL,
    GATEWAY,
    SERVING,
    BROWSE,
    FEEDBACK_DOC,
    FEEDBACK_CODE,
    FEEDBACK_CORE,
    ROOT_README,
    RUNBOOK,
    GEMINI_SPEC,
    HOUSEKEEPING,
):
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
root_readme = ROOT_README.read_text(encoding="utf-8")
runbook = RUNBOOK.read_text(encoding="utf-8")
gemini_spec = GEMINI_SPEC.read_text(encoding="utf-8")
housekeeping = HOUSEKEEPING.read_text(encoding="utf-8")

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
    "TheseusSessionSearch.session_feedback_get",
    "TheseusSessionSearch.session_browse",
    "TheseusSessionSearch.session_coverage",
    "exposes **five** consumer tools",
    "VERIFIED_TO_PROVIDER_REPORTED_WATERMARK",
    "STALE_AFTER_PROVIDER_REPORTED_WATERMARK",
    "UNKNOWN_WITHOUT_SOURCE_WATERMARK",
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

for label, document in (
    ("overlay README", readme),
    ("search skill", skill),
    ("gateway", gateway),
    ("serving plane", serving),
    ("root README", root_readme),
    ("promotion runbook", runbook),
):
    for marker in (
        "VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT",
        "STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT",
    ):
        if marker not in document:
            raise SystemExit(
                f"SESSION_SEARCH_APP_OVERLAY_FAIL {label} Gemini snapshot marker: {marker}"
            )

for stale in (
    "If the gateway lists three tools but this host still discovers only the earlier two",
    "tools_count=3",
):
    if stale in browse:
        raise SystemExit(
            f"SESSION_SEARCH_APP_OVERLAY_FAIL stale browse discovery checkpoint: {stale}"
        )

for label, document in (
    ("overlay README", readme),
    ("gateway", gateway),
    ("serving plane", serving),
):
    for marker in ("gemini-0001", "five READY shards"):
        if marker not in document:
            raise SystemExit(
                f"SESSION_SEARCH_APP_OVERLAY_FAIL {label} live Gemini marker: {marker}"
            )

if "GEMINI LIVE / FIVE-SHARD PRODUCTION VERIFIED" not in gemini_spec:
    raise SystemExit("SESSION_SEARCH_APP_OVERLAY_FAIL Gemini spec terminal state missing")

if "live five shard generations" not in housekeeping:
    raise SystemExit("SESSION_SEARCH_APP_OVERLAY_FAIL housekeeping live five-shard retention missing")
if "live four shard generations" in housekeeping:
    raise SystemExit("SESSION_SEARCH_APP_OVERLAY_FAIL housekeeping stale four-shard retention")

for label, document in (("search skill", skill), ("serving plane", serving)):
    if "exe.dev first" not in document:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL {label} exe.dev-first route missing")
    if "MarcoPolo may be used for engineering" in document or "MarcoPolo remains an engineering" in document:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL {label} stale MarcoPolo-first-era wording")

for marker in ("OUTSIDE_OBSERVED_INDEX_RANGE", "OBSERVED_POSSIBLE", "UNKNOWN_COVERAGE"):
    if marker not in skill:
        raise SystemExit(f"SESSION_SEARCH_APP_OVERLAY_FAIL search skill window-fit marker: {marker}")

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
    "TheseusSessionSearch.session_search",
    "TheseusSessionSearch.session_browse",
    "TheseusSessionSearch.session_coverage",
    "TheseusSessionSearch.session_feedback",
    "TheseusSessionSearch.session_feedback_get",
    "VERIFIED_TO_PROVIDER_REPORTED_WATERMARK",
    "STALE_AFTER_PROVIDER_REPORTED_WATERMARK",
    "UNKNOWN_WITHOUT_SOURCE_WATERMARK",
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
