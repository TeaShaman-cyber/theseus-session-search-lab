#!/usr/bin/env python3
"""Issue 184: Azure Translator v3 Dynamic Dictionary against existing frozen baseline.

Offline only: exact frozen #120/#127, no LLM and no serving changes.
"""
from __future__ import annotations

import argparse
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent


def _import(name, path):
    spec = importlib.util.spec_from_file_location(name, HERE / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_AZURE = _import("issue184_base", "azure_benchmark_runner.py")
_TERMS = _import("issue184_terms", "azure_v3_protected_runner.py")
EVAL_SCHEMA = "issue184_v3_dynamic"


def dictionary_markup(source: str) -> tuple[str, tuple[str, ...]]:
    pieces = []
    terms = []
    last = 0
    for match in _TERMS._TERM_RE.finditer(source):
        value = match.group(0)
        pieces.append(source[last:match.start()])
        safe = html.escape(value, quote=True)
        pieces.append(
            '<mstrans:dictionary translation="' + safe
            + '">' + safe + '</mstrans:dictionary>'
        )
        if value not in terms:
            terms.append(value)
        last = match.end()
    pieces.append(source[last:])
    return "".join(pieces), tuple(terms)


def validate_output(text: str, terms: tuple[str, ...]) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("DICT_EMPTY_RESULT")
    if re.search(r"(?:<|&lt;)\s*/?\s*mstrans:dictionary\b", text, re.IGNORECASE):
        raise ValueError("DICT_TAG_LEAK")
    for term in terms:
        if term not in text:
            raise ValueError("DICT_TERM_MISSING")
    return text.strip()


def translate_rows(en_rows, *, token: str, endpoint: str):
    if "api-version=3.0" not in endpoint or "from=en" not in endpoint or "to=ru" not in endpoint:
        raise ValueError("V3_EN_RU_ENDPOINT_REQUIRED")
    out = []
    for row in sorted(en_rows, key=lambda r: r["case_id"]):
        marked, terms = dictionary_markup(row["query_text"])
        body = json.dumps([{"Text": marked}], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                latency_ms = (time.perf_counter() - started) * 1000
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"DYNAMIC_TRANSLATOR_HTTP_{exc.code}") from None
        if len(payload) != 1 or len(payload[0].get("translations", [])) != 1:
            raise RuntimeError("DYNAMIC_RESPONSE_SHAPE_DRIFT")
        translated = validate_output(payload[0]["translations"][0]["text"], terms)
        out.append({
            "case_id": row["case_id"],
            "query_text": row["query_text"],
            "target_content_fp": row["target_content_fp"],
            "translated_ru": translated,
            "latency_ms": latency_ms,
            "protected_terms": len(terms),
            "sent_chars": len(marked),
        })
    return out


def save_private(path: Path, payload: dict):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def runtime():
    base = os.getenv("ISSUE127_NEON_API_BASE")
    if not base:
        raise SystemExit("ISSUE127_NEON_API_BASE_REQUIRED")
    return _AZURE.postgres_runtime_from_neon_api(
        api_base=base,
        project_id=os.getenv("ISSUE127_NEON_PROJECT_ID", "wild-surf-61625738"),
        branch_id=os.getenv("ISSUE127_NEON_BRANCH_ID", "br-sweet-dream-b5qzaztb"),
        role_name=os.getenv("ISSUE127_NEON_ROLE", "neondb_owner"),
    )


def translate(bundle: Path):
    rt = runtime()
    gold = _AZURE._fetch_gold(rt)
    summary = _AZURE.validate_frozen_gold(gold)
    _AZURE.EVAL_SCHEMA = EVAL_SCHEMA
    if not _AZURE._schema_absent(rt):
        raise SystemExit("SCHEMA_ALREADY_EXISTS")
    en = [r for r in gold if r["query_lang"] == "en"]
    endpoint = os.getenv("ISSUE127_TRANSLATOR_ENDPOINT", _AZURE.DEFAULT_TRANSLATOR_ENDPOINT)
    token = _AZURE._get_access_token(os.getenv("ISSUE127_AZ_OWNER", _AZURE.DEFAULT_AZ_OWNER))
    rows = translate_rows(en, token=token, endpoint=endpoint)
    save_private(bundle, {"gold": summary, "rows": rows})
    print(json.dumps({
        "phase": "translation_frozen", "calls": len(rows),
        "protected_spans": sum(r["protected_terms"] for r in rows),
        "plain_chars": sum(len(r["query_text"]) for r in rows),
        "marked_chars": sum(r["sent_chars"] for r in rows),
        "mean_latency_ms": sum(r["latency_ms"] for r in rows) / len(rows),
        "bundle_bytes": bundle.stat().st_size,
    }))


def evaluate(bundle: Path):
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    if payload["gold"]["gold_digest_v2"] != _AZURE.EXPECTED_GOLD_DIGEST_V2:
        raise ValueError("GOLD_DIGEST_MISMATCH")
    rows = payload["rows"]
    if len(rows) != 10 or len({r["case_id"] for r in rows}) != 10:
        raise ValueError("CASE_SET_DRIFT")
    rt = runtime()
    _AZURE.EVAL_SCHEMA = EVAL_SCHEMA
    if not _AZURE._schema_absent(rt):
        raise SystemExit("SCHEMA_ALREADY_EXISTS")
    _AZURE._freeze_translations(rt, rows)
    digest = _AZURE.translation_digest(rows)
    if _AZURE.translation_digest(_AZURE._readback_translation_rows(rt)) != digest:
        raise RuntimeError("TRANSLATION_READBACK_MISMATCH")
    result = _AZURE._evaluate(rt)
    receipt = _AZURE.public_receipt(
        provider_rows=rows,
        ranks={r["case_id"]: r["target_rank"] for r in result},
        gold_digest_v2=_AZURE.EXPECTED_GOLD_DIGEST_V2,
        translation_digest_v2=digest,
        historical_translation_digest=_AZURE.historical_translation_digest(rows),
    )
    receipt["per_case"] = [{"case_id": r["case_id"], "target_rank": r["target_rank"]} for r in result]
    print(json.dumps({"phase": "evaluated", "receipt": receipt}, ensure_ascii=False))


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
