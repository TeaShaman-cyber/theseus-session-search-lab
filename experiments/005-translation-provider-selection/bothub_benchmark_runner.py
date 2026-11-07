#!/usr/bin/env python3
"""Run frozen #176 BotHub paid-model translation benchmark."""

from __future__ import annotations

import importlib.util
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
AZURE_RUNNER_PATH = HERE / "azure_benchmark_runner.py"
_SPEC = importlib.util.spec_from_file_location(
    "issue127_azure_benchmark_runner", AZURE_RUNNER_PATH
)
_AZURE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_AZURE)

EXPECTED_GOLD_DIGEST_V2 = _AZURE.EXPECTED_GOLD_DIGEST_V2
DEFAULT_ENDPOINT = "https://bothub-api.int.exe.xyz/v1/chat/completions"

TRANSLATION_SYSTEM_PROMPT = (
    "You are a translation engine. Translate the English user query into Russian "
    "faithfully for lexical information retrieval. Preserve technical terms, "
    "proper nouns, product names, acronyms, identifiers, and distinctions in "
    "meaning. Do not summarize or answer the query. Output only the Russian "
    "translation, with no explanation, labels, markdown, or quotes."
)

# BotHub public RUB tariff snapshot observed on 2026-11-07.
CANDIDATE_ARMS = [
    {
        "name": "gemini_flash_lite",
        "model": "gemini-2.5-flash-lite",
        "response_model": "gemini-2.5-flash-lite",
        "schema": "issue176_gemini_flash_lite",
        "input_rub_per_m": 4.38,
        "output_rub_per_m": 17.53,
    },
    {
        "name": "gpt41_nano",
        "model": "gpt-4.1-nano",
        "response_model": "openai/gpt-4.1-nano",
        "schema": "issue176_gpt41_nano",
        "input_rub_per_m": 4.38,
        "output_rub_per_m": 17.53,
    },
    {
        "name": "deepseek_v41_flash",
        "model": "deepseek-v4.1-flash",
        "response_model": "deepseek/deepseek-v4.1-flash",
        "schema": "issue176_deepseek_v41_flash",
        "input_rub_per_m": 4.25,
        "output_rub_per_m": 47.21,
    },
]


class ProviderCallError(RuntimeError):
    pass


def select_arms(spec: str | None):
    if not spec:
        return list(CANDIDATE_ARMS)
    wanted = [part.strip() for part in spec.split(",") if part.strip()]
    by_name = {arm["name"]: arm for arm in CANDIDATE_ARMS}
    unknown = [name for name in wanted if name not in by_name]
    if unknown:
        raise ValueError("UNKNOWN_ARM=" + ",".join(unknown))
    wanted_set = set(wanted)
    return [arm for arm in CANDIDATE_ARMS if arm["name"] in wanted_set]


def build_request_payload(*, arm, query_text: str) -> dict:
    return {
        "model": arm["model"],
        "messages": [
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {"role": "user", "content": query_text},
        ],
        "temperature": 0,
        "max_tokens": 512,
        "stream": False,
    }


def parse_response(*, arm, payload: dict) -> dict:
    model = payload.get("model")
    expected_model = arm["response_model"]
    if model != expected_model:
        raise RuntimeError(
            f"MODEL_DRIFT expected={expected_model} actual={model}"
        )

    choices = payload.get("choices") or []
    if len(choices) != 1:
        raise RuntimeError(f"CHOICE_COUNT={len(choices)}")
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if finish_reason != "stop":
        raise RuntimeError(f"FINISH_REASON={finish_reason}")

    message = choice.get("message") or {}
    translated = message.get("content")
    if not isinstance(translated, str) or not translated.strip():
        raise RuntimeError("EMPTY_TRANSLATION")

    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    return {
        "translated_ru": translated.strip(),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "reasoning_tokens": int(details.get("reasoning_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "provider_cost": usage.get("cost"),
        "response_model": model,
    }


def estimated_rub(*, arm, provider_rows) -> float:
    prompt = sum(int(row.get("prompt_tokens") or 0) for row in provider_rows)
    completion = sum(int(row.get("completion_tokens") or 0) for row in provider_rows)
    return (
        prompt * arm["input_rub_per_m"]
        + completion * arm["output_rub_per_m"]
    ) / 1_000_000.0


def translate_rows(*, arm, en_rows, endpoint: str = DEFAULT_ENDPOINT):
    rows = []
    for row in sorted(en_rows, key=lambda item: item["case_id"]):
        body = build_request_payload(arm=arm, query_text=row["query_text"])
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(),
            method="POST",
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                latency_ms = (time.perf_counter() - started) * 1000.0
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ProviderCallError(f"BOTHUB_HTTP_{exc.code}") from None

        try:
            parsed = parse_response(arm=arm, payload=payload)
        except RuntimeError as exc:
            raise ProviderCallError(str(exc)) from None

        rows.append(
            {
                "case_id": row["case_id"],
                "query_text": row["query_text"],
                "translated_ru": parsed["translated_ru"],
                "target_content_fp": row["target_content_fp"],
                "latency_ms": latency_ms,
                **parsed,
            }
        )
    return rows


def runtime_from_environment():
    database_url = os.environ.get("ISSUE176_DATABASE_URL")
    if database_url:
        return _AZURE.postgres_runtime(database_url)

    api_base = os.environ.get("ISSUE176_NEON_API_BASE")
    if not api_base:
        raise SystemExit("ISSUE176_DATABASE_URL_OR_NEON_API_BASE_REQUIRED")

    return _AZURE.postgres_runtime_from_neon_api(
        api_base=api_base,
        project_id=os.environ.get(
            "ISSUE176_NEON_PROJECT_ID", "wild-surf-61625738"
        ),
        branch_id=os.environ.get(
            "ISSUE176_NEON_BRANCH_ID", "br-sweet-dream-b5qzaztb"
        ),
        role_name=os.environ.get("ISSUE176_NEON_ROLE", "neondb_owner"),
    )


def schema_absent(runtime, schema: str) -> bool:
    count = _AZURE._run_psql(
        runtime,
        sql=(
            "select count(*) from information_schema.schemata "
            f"where schema_name='{schema}'"
        ),
    )
    return int(count) == 0


def public_receipt(*, arm, provider_rows, results, gold_digest_v2):
    ranks = {row["case_id"]: row["target_rank"] for row in results}
    receipt = _AZURE.public_receipt(
        provider_rows=provider_rows,
        ranks=ranks,
        gold_digest_v2=gold_digest_v2,
        translation_digest_v2=_AZURE.translation_digest(provider_rows),
        historical_translation_digest=_AZURE.historical_translation_digest(
            provider_rows
        ),
    )
    receipt.update(
        {
            "arm": arm["name"],
            "requested_model": arm["model"],
            "response_model": provider_rows[0]["response_model"],
            "prompt_tokens": sum(r["prompt_tokens"] for r in provider_rows),
            "completion_tokens": sum(r["completion_tokens"] for r in provider_rows),
            "reasoning_tokens": sum(r["reasoning_tokens"] for r in provider_rows),
            "total_tokens": sum(r["total_tokens"] for r in provider_rows),
            "provider_cost_reported": [
                r["provider_cost"] for r in provider_rows
            ],
            "estimated_rub": estimated_rub(
                arm=arm, provider_rows=provider_rows
            ),
            "pricing_snapshot": {
                "date": "2026-11-07",
                "input_rub_per_m": arm["input_rub_per_m"],
                "output_rub_per_m": arm["output_rub_per_m"],
            },
            "per_case": [
                {
                    "case_id": row["case_id"],
                    "target_rank": row["target_rank"],
                    "candidate_count": row["candidate_count"],
                    "selected_terms": row["selected_terms"],
                    "positive_terms": row["positive_terms"],
                }
                for row in results
            ],
        }
    )
    return receipt


def main() -> int:
    runtime = runtime_from_environment()
    selected_arms = select_arms(os.environ.get("ISSUE176_ARMS"))

    for arm in selected_arms:
        if not schema_absent(runtime, arm["schema"]):
            raise SystemExit(f"ISSUE176_SCHEMA_ALREADY_EXISTS={arm['schema']}")

    gold_rows = _AZURE._fetch_gold(runtime)
    gold = _AZURE.validate_frozen_gold(
        gold_rows, expected_digest=EXPECTED_GOLD_DIGEST_V2
    )
    en_rows = [row for row in gold_rows if row["query_lang"] == "en"]
    endpoint = os.environ.get("ISSUE176_BOTHUB_ENDPOINT", DEFAULT_ENDPOINT)

    # Phase 1: complete every translation arm before inspecting retrieval.
    translated = {}
    failures = {}
    for arm in selected_arms:
        try:
            translated[arm["name"]] = translate_rows(
                arm=arm, en_rows=en_rows, endpoint=endpoint
            )
        except ProviderCallError as exc:
            failures[arm["name"]] = str(exc)

    # Phase 2: freeze successful arms before retrieval inspection.
    frozen = {}
    for arm in selected_arms:
        rows = translated.get(arm["name"])
        if rows is None:
            continue
        _AZURE.EVAL_SCHEMA = arm["schema"]
        _AZURE._freeze_translations(runtime, rows)
        readback = _AZURE._readback_translation_rows(runtime)
        if _AZURE.translation_digest(readback) != _AZURE.translation_digest(rows):
            raise RuntimeError(
                f"TRANSLATION_READBACK_DIGEST_MISMATCH arm={arm['name']}"
            )
        frozen[arm["name"]] = rows

    # Phase 3: only now inspect retrieval.
    receipts = []
    for arm in selected_arms:
        rows = frozen.get(arm["name"])
        if rows is None:
            continue
        _AZURE.EVAL_SCHEMA = arm["schema"]
        results = _AZURE._evaluate(runtime)
        receipts.append(
            public_receipt(
                arm=arm,
                provider_rows=rows,
                results=results,
                gold_digest_v2=gold["gold_digest_v2"],
            )
        )

    print(
        json.dumps(
            {"gold": gold, "arms": receipts, "translation_failures": failures},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
