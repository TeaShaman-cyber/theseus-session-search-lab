#!/usr/bin/env python3
"""Issue #182: paired Azure v3 plain and terminology-preserving frozen retrieval test.

No LLM or production deployment. Private translations remain only in a mode-0600
runtime bundle and the existing restricted evaluation database.
"""
from __future__ import annotations

import argparse
import html
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

BASE = Path(__file__).with_name("azure_benchmark_runner.py")
_SPEC = importlib.util.spec_from_file_location("issue182_azure_base", BASE)
_AZURE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_AZURE)

# Conservative, pre-approved proper/product names and unambiguous acronyms.
# No generic technical words, inferred replacements or case folding.
CANONICAL_TERMS = (
    "Basic Memory", "Google Drive", "PostgreSQL", "DeepSeek", "Infisical",
    "MarcoPolo", "GitHub", "Hindsight", "Notion", "Gramps", "SQLite",
    "OAuth", "MCP", "Linux",
)
_TERM_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(t) for t in sorted(CANONICAL_TERMS, key=len, reverse=True)) + r")(?!\w)"
)
SCHEMAS = {"plain": "issue182_v3_plain", "protected": "issue182_v3_protected"}


def eval_schema(arm: str) -> str:
    if arm not in SCHEMAS:
        raise ValueError("UNKNOWN_ARM")
    return SCHEMAS[arm]


def markup_query(source: str) -> tuple[str, tuple[str, ...]]:
    pieces: list[str] = []
    protected: list[str] = []
    pos = 0
    for match in _TERM_RE.finditer(source):
        pieces.append(html.escape(source[pos:match.start()], quote=True))
        original = match.group(0)
        pieces.append('<span class="notranslate">' + html.escape(original) + '</span>')
        if original not in protected:
            protected.append(original)
        pos = match.end()
    pieces.append(html.escape(source[pos:], quote=True))
    return '<div>' + ''.join(pieces) + '</div>', tuple(protected)


class _StrictHTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.tag_count = 0

    def handle_starttag(self, tag, attrs):
        if tag not in ("div", "span"):
            raise ValueError("UNEXPECTED_HTML_TAG")
        self.stack.append(tag)
        self.tag_count += 1

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise ValueError("UNBALANCED_HTML")
        
    def handle_startendtag(self, tag, attrs):
        raise ValueError("UNEXPECTED_HTML_TAG")

    def handle_data(self, data):
        self.parts.append(data)


def clean_translation(body: str, protected: tuple[str, ...]) -> str:
    parser = _StrictHTMLText()
    parser.feed(body)
    parser.close()
    if parser.stack:
        raise ValueError("UNBALANCED_HTML")
    text = ''.join(parser.parts).strip()
    if not text:
        raise ValueError("EMPTY_TRANSLATION")
    if re.search(r"<\s*/?\s*(?:span|div)\b", text, re.IGNORECASE):
        raise ValueError("MARKUP_LEAK")
    for item in protected:
        if item not in text:
            raise ValueError("PROTECTED_TERM_MISSING")
    return text


def _runtime():
    base = os.environ.get("ISSUE127_NEON_API_BASE")
    if not base:
        raise SystemExit("ISSUE127_NEON_API_BASE_REQUIRED")
    return _AZURE.postgres_runtime_from_neon_api(
        api_base=base,
        project_id=os.environ.get("ISSUE127_NEON_PROJECT_ID", "wild-surf-61625738"),
        branch_id=os.environ.get("ISSUE127_NEON_BRANCH_ID", "br-sweet-dream-b5qzaztb"),
        role_name=os.environ.get("ISSUE127_NEON_ROLE", "neondb_owner"),
    )


def _protected_translate(rows, *, token: str, endpoint: str):
    if '?' not in endpoint:
        raise ValueError("ENDPOINT_QUERY_REQUIRED")
    protected_endpoint = endpoint + "&textType=html"
    result = []
    for row in sorted(rows, key=lambda r: r["case_id"]):
        marked, terms = markup_query(row["query_text"])
        body = json.dumps([{"Text": marked}], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            protected_endpoint,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                elapsed = (time.perf_counter() - start) * 1000
                payload = json.load(response)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"PROTECTED_TRANSLATOR_HTTP_{e.code}") from None
        if len(payload) != 1 or len(payload[0].get("translations", [])) != 1:
            raise RuntimeError("UNEXPECTED_TRANSLATOR_RESPONSE")
        text = clean_translation(payload[0]["translations"][0]["text"], terms)
        result.append({
            "case_id": row["case_id"], "query_text": row["query_text"],
            "target_content_fp": row["target_content_fp"],
            "translated_ru": text, "latency_ms": elapsed,
            "protected_terms": len(terms), "sent_characters": len(marked),
        })
    return result


def _save_private(path: Path, bundle: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)


def translate(bundle_path: Path) -> None:
    runtime = _runtime()
    rows = _AZURE._fetch_gold(runtime)
    gold = _AZURE.validate_frozen_gold(rows)
    for arm in SCHEMAS:
        _AZURE.EVAL_SCHEMA = eval_schema(arm)
        if not _AZURE._schema_absent(runtime):
            raise SystemExit("ISSUE182_EVAL_SCHEMA_ALREADY_EXISTS")
    en = [r for r in rows if r["query_lang"] == "en"]
    token = _AZURE._get_access_token(os.environ.get("ISSUE127_AZ_OWNER", _AZURE.DEFAULT_AZ_OWNER))
    endpoint = os.environ.get("ISSUE127_TRANSLATOR_ENDPOINT", _AZURE.DEFAULT_TRANSLATOR_ENDPOINT)
    plain = _AZURE._translate_rows(en, endpoint=endpoint, token=token)
    protected = _protected_translate(en, endpoint=endpoint, token=token)
    _save_private(bundle_path, {"gold": gold, "plain": plain, "protected": protected})
    print(json.dumps({
        "phase": "translation_frozen", "rows_per_arm": len(en),
        "calls": len(plain) + len(protected),
        "protected_spans": sum(r["protected_terms"] for r in protected),
        "protected_input_chars": sum(r["sent_characters"] for r in protected),
        "plain_input_chars": sum(len(r["query_text"]) for r in plain),
        "translation_changes": sum(a["translated_ru"] != b["translated_ru"] for a, b in zip(plain, protected)),
        "bundle_bytes": bundle_path.stat().st_size,
    }))


def evaluate(bundle_path: Path) -> None:
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    if payload["gold"]["gold_digest_v2"] != _AZURE.EXPECTED_GOLD_DIGEST_V2:
        raise ValueError("GOLD_DIGEST_MISMATCH")
    runtime = _runtime()
    receipts = {}
    for arm in SCHEMAS:
        _AZURE.EVAL_SCHEMA = eval_schema(arm)
        if not _AZURE._schema_absent(runtime):
            raise SystemExit(f"ISSUE182_SCHEMA_ALREADY_EXISTS_{arm}")
        provider_rows = payload[arm]
        _AZURE._freeze_translations(runtime, provider_rows)
        digest = _AZURE.translation_digest(provider_rows)
        if _AZURE.translation_digest(_AZURE._readback_translation_rows(runtime)) != digest:
            raise RuntimeError("TRANSLATION_READBACK_MISMATCH")
        results = _AZURE._evaluate(runtime)
        ranks = {r["case_id"]: r["target_rank"] for r in results}
        receipts[arm] = _AZURE.public_receipt(
            provider_rows=provider_rows, ranks=ranks,
            gold_digest_v2=_AZURE.EXPECTED_GOLD_DIGEST_V2,
            translation_digest_v2=digest,
            historical_translation_digest=_AZURE.historical_translation_digest(provider_rows),
        )
        receipts[arm]["per_case"] = [
            {"case_id": row["case_id"], "target_rank": row["target_rank"]}
            for row in results
        ]
    print(json.dumps({"phase": "evaluated", "arms": receipts}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("translate", "evaluate"))
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "translate":
        translate(args.bundle)
    else:
        evaluate(args.bundle)


if __name__ == "__main__":
    main()
