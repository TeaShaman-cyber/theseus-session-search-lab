#!/usr/bin/env python3
"""Issue #178: Azure draft + local Phi repair on frozen translation set."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
AZURE_PATH = HERE / "azure_benchmark_runner.py"
_SPEC = importlib.util.spec_from_file_location("issue127_azure", AZURE_PATH)
_AZURE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_AZURE)

EXPECTED_GOLD_DIGEST_V2 = _AZURE.EXPECTED_GOLD_DIGEST_V2
EVAL_SCHEMA = "issue178_azure_phi"
DEFAULT_PHI_ENDPOINT = "http://127.0.0.1:8080/v1/chat/completions"

SYSTEM_PROMPT = (
    "You conservatively repair a Russian machine translation of an English "
    "technical search query. Preserve the original meaning. Preserve or restore "
    "technical terms, proper nouns, product names, acronyms, identifiers, and "
    "important distinctions. Do not answer, summarize, explain, or add facts. "
    "Output only the repaired Russian query."
)


def runtime_metadata(*, model: str, quant: str, runtime: str) -> dict:
    return {
        "model": model,
        "quant": quant,
        "runtime": runtime,
    }


def build_messages(*, query_en: str, azure_ru: str):
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "English source:\n"
                + query_en
                + "\n\nRussian machine-translation draft:\n"
                + azure_ru
            ),
        },
    ]


def parse_phi_response(payload: dict) -> dict:
    choices = payload.get("choices") or []
    if len(choices) != 1:
        raise RuntimeError(f"CHOICE_COUNT={len(choices)}")
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if finish_reason != "stop":
        raise RuntimeError(f"FINISH_REASON={finish_reason}")
    message = choice.get("message") or {}
    repaired = message.get("content")
    if not isinstance(repaired, str) or not repaired.strip():
        raise RuntimeError("EMPTY_REPAIR")
    usage = payload.get("usage") or {}
    return {
        "repaired_ru": repaired.strip(),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def validate_repaired_rows(source_rows, repaired_rows):
    source = {row["case_id"]: row for row in source_rows}
    repaired = {row["case_id"]: row for row in repaired_rows}
    if set(source) != set(repaired):
        raise ValueError("CASE_SET_DRIFT")
    for case_id, src in source.items():
        row = repaired[case_id]
        if row.get("target_content_fp") != src.get("target_content_fp"):
            raise ValueError(f"TARGET_MAPPING_DRIFT case={case_id}")
        if row.get("query_text") != src.get("query_text"):
            raise ValueError(f"QUERY_MAPPING_DRIFT case={case_id}")
        if row.get("azure_ru") != src.get("azure_ru"):
            raise ValueError(f"AZURE_DRAFT_DRIFT case={case_id}")
        if not str(row.get("translated_ru") or "").strip():
            raise ValueError(f"EMPTY_REPAIR case={case_id}")


def _runtime():
    api_base = os.environ.get("ISSUE178_NEON_API_BASE")
    if not api_base:
        raise SystemExit("ISSUE178_NEON_API_BASE_REQUIRED")
    return _AZURE.postgres_runtime_from_neon_api(
        api_base=api_base,
        project_id=os.environ.get(
            "ISSUE178_NEON_PROJECT_ID", "wild-surf-61625738"
        ),
        branch_id=os.environ.get(
            "ISSUE178_NEON_BRANCH_ID", "br-sweet-dream-b5qzaztb"
        ),
        role_name=os.environ.get("ISSUE178_NEON_ROLE", "neondb_owner"),
    )


def prepare(path: str):
    runtime = _runtime()
    gold = _AZURE._fetch_gold(runtime)
    summary = _AZURE.validate_frozen_gold(
        gold, expected_digest=EXPECTED_GOLD_DIGEST_V2
    )
    en = [row for row in gold if row["query_lang"] == "en"]
    token = _AZURE._get_access_token(_AZURE.DEFAULT_AZ_OWNER)
    translated = _AZURE._translate_rows(
        en, endpoint=_AZURE.DEFAULT_TRANSLATOR_ENDPOINT, token=token
    )
    rows = [
        {
            "case_id": row["case_id"],
            "query_text": row["query_text"],
            "azure_ru": row["translated_ru"],
            "target_content_fp": row["target_content_fp"],
        }
        for row in translated
    ]
    Path(path).write_text(
        json.dumps({"gold": summary, "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    print(json.dumps({"mode": "prepare", "rows": len(rows), "gold": summary}))


def repair(
    input_path: str,
    output_path: str,
    *,
    endpoint: str = DEFAULT_PHI_ENDPOINT,
    model: str = "local-model",
):
    payload = json.loads(Path(input_path).read_text(encoding="utf-8-sig"))
    rows = payload["rows"]
    repaired = []
    for row in sorted(rows, key=lambda item: item["case_id"]):
        body = {
            "model": model,
            "messages": build_messages(
                query_en=row["query_text"], azure_ru=row["azure_ru"]
            ),
            "temperature": 0,
            "seed": 178,
            "max_tokens": 192,
            "stream": False,
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "theseus-issue178/1",
            },
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                latency_ms = (time.perf_counter() - started) * 1000.0
                response_payload = json.loads(
                    response.read().decode("utf-8")
                )
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"PHI_HTTP_{exc.code}") from None
        parsed = parse_phi_response(response_payload)
        repaired.append(
            {
                **row,
                "translated_ru": parsed["repaired_ru"],
                "latency_ms": latency_ms,
                "prompt_tokens": parsed["prompt_tokens"],
                "completion_tokens": parsed["completion_tokens"],
                "total_tokens": parsed["total_tokens"],
            }
        )

    validate_repaired_rows(rows, repaired)
    Path(output_path).write_text(
        json.dumps(
            {"gold": payload["gold"], "rows": repaired},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "mode": "repair",
                "rows": len(repaired),
                "mean_latency_ms": sum(r["latency_ms"] for r in repaired)
                / len(repaired),
                "prompt_tokens": sum(r["prompt_tokens"] for r in repaired),
                "completion_tokens": sum(
                    r["completion_tokens"] for r in repaired
                ),
            },
            separators=(",", ":"),
        )
    )


def evaluate(
    input_path: str,
    *,
    model: str,
    quant: str,
    runtime_label: str,
):
    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if payload["gold"]["gold_digest_v2"] != EXPECTED_GOLD_DIGEST_V2:
        raise ValueError("GOLD_DIGEST_MISMATCH")

    repaired = payload["rows"]
    source_rows = [
        {
            "case_id": r["case_id"],
            "query_text": r["query_text"],
            "azure_ru": r["azure_ru"],
            "target_content_fp": r["target_content_fp"],
        }
        for r in repaired
    ]
    validate_repaired_rows(source_rows, repaired)

    runtime = _runtime()
    _AZURE.EVAL_SCHEMA = EVAL_SCHEMA
    if not _AZURE._schema_absent(runtime):
        raise SystemExit(f"ISSUE178_SCHEMA_ALREADY_EXISTS={EVAL_SCHEMA}")

    provider_rows = [
        {
            "case_id": r["case_id"],
            "query_text": r["query_text"],
            "translated_ru": r["translated_ru"],
            "target_content_fp": r["target_content_fp"],
            "latency_ms": r["latency_ms"],
        }
        for r in repaired
    ]

    _AZURE._freeze_translations(runtime, provider_rows)
    readback = _AZURE._readback_translation_rows(runtime)
    digest = _AZURE.translation_digest(provider_rows)
    if _AZURE.translation_digest(readback) != digest:
        raise RuntimeError("TRANSLATION_READBACK_DIGEST_MISMATCH")

    results = _AZURE._evaluate(runtime)
    ranks = {row["case_id"]: row["target_rank"] for row in results}
    receipt = _AZURE.public_receipt(
        provider_rows=provider_rows,
        ranks=ranks,
        gold_digest_v2=EXPECTED_GOLD_DIGEST_V2,
        translation_digest_v2=digest,
        historical_translation_digest=_AZURE.historical_translation_digest(
            provider_rows
        ),
    )
    receipt.update(
        {
            **runtime_metadata(
                model=model,
                quant=quant,
                runtime=runtime_label,
            ),
            "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in repaired),
            "completion_tokens": sum(
                r.get("completion_tokens", 0) for r in repaired
            ),
            "total_tokens": sum(r.get("total_tokens", 0) for r in repaired),
            "per_case": [
                {
                    "case_id": row["case_id"],
                    "target_rank": row["target_rank"],
                }
                for row in results
            ],
        }
    )
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("--output", required=True)

    p = sub.add_parser("repair")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--endpoint", default=DEFAULT_PHI_ENDPOINT)
    p.add_argument("--model", default="local-model")

    p = sub.add_parser("evaluate")
    p.add_argument("--input", required=True)
    p.add_argument(
        "--model",
        default="microsoft/Phi-4-mini-instruct",
    )
    p.add_argument("--quant", default="Q4_K_M")
    p.add_argument(
        "--runtime-label",
        default="LM Studio native Windows",
    )

    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.output)
    elif args.mode == "repair":
        repair(
            args.input,
            args.output,
            endpoint=args.endpoint,
            model=args.model,
        )
    else:
        evaluate(
            args.input,
            model=args.model,
            quant=args.quant,
            runtime_label=args.runtime_label,
        )


if __name__ == "__main__":
    main()
