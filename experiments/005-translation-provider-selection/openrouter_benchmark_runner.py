#!/usr/bin/env python3
"""Run frozen #174 OpenRouter translation arms through the #127 evaluator.

The OpenRouter credential is injected by exe.dev's native catalog integration.
No API key, database password, query text, translation text, or target fingerprint
is printed by this runner.
"""

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
_AZURE_SPEC = importlib.util.spec_from_file_location(
    "issue127_azure_benchmark_runner", AZURE_RUNNER_PATH
)
_AZURE = importlib.util.module_from_spec(_AZURE_SPEC)
assert _AZURE_SPEC.loader is not None
_AZURE_SPEC.loader.exec_module(_AZURE)

EXPECTED_GOLD_DIGEST_V2 = _AZURE.EXPECTED_GOLD_DIGEST_V2
DEFAULT_OPENROUTER_ENDPOINT = (
    "https://openrouter.int.exe.xyz/api/v1/chat/completions"
)

TRANSLATION_SYSTEM_PROMPT = (
    "You are a translation engine. Translate the English user query into Russian "
    "faithfully for lexical information retrieval. Preserve technical terms, "
    "proper nouns, product names, acronyms, identifiers, and distinctions in "
    "meaning. Do not summarize or answer the query. Output only the Russian "
    "translation, with no explanation, labels, markdown, or quotes."
)



class ProviderCallError(RuntimeError):
    """Provider/model call failed before a complete translation arm was frozen."""


CANDIDATE_ARMS = [
    {
        "name": "xing4",
        "model": "xingchen-agi/xing4.0-29b-a4b:free",
        "provider": "phala",
        "response_provider": "Phala",
        "schema": "issue174_xing4",
    },
    {
        "name": "gemma4",
        "model": "google/gemma-4-31b-it:free",
        "provider": "google-ai-studio",
        "response_provider": "Google AI Studio",
        "schema": "issue174_gemma4",
    },
    {
        "name": "nemotron3",
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "provider": "nvidia",
        "response_provider": "Nvidia",
        "schema": "issue174_nemotron3",
    },
]


def build_request_payload(*, arm, query_text: str) -> dict:
    if arm["model"] == "openrouter/free":
        raise ValueError("ROUTER_ALIAS_FORBIDDEN")

    return {
        "model": arm["model"],
        "messages": [
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {"role": "user", "content": query_text},
        ],
        "temperature": 0,
        "seed": 127,
        "max_tokens": 512,
        "reasoning": {"enabled": False},
        "provider": {
            "only": [arm["provider"]],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
    }


def parse_response(*, arm, payload: dict) -> dict:
    model = payload.get("model")
    if model != arm["model"]:
        raise RuntimeError(
            f"MODEL_DRIFT expected={arm['model']} actual={model}"
        )

    provider = payload.get("provider")
    if provider != arm["response_provider"]:
        raise RuntimeError(
            f"PROVIDER_DRIFT expected={arm['response_provider']} actual={provider}"
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
    completion_details = usage.get("completion_tokens_details") or {}
    reasoning_tokens = completion_details.get("reasoning_tokens", 0)
    if reasoning_tokens not in (0, None):
        raise RuntimeError(f"UNEXPECTED_REASONING_TOKENS={reasoning_tokens}")

    return {
        "translated_ru": translated.strip(),
        "cost": usage.get("cost"),
        "model": model,
        "provider": provider,
    }


def translate_rows(
    *,
    arm,
    en_rows,
    endpoint: str = DEFAULT_OPENROUTER_ENDPOINT,
    min_start_interval_seconds: float = 3.2,
):
    translated = []
    previous_start = None
    for row in sorted(en_rows, key=lambda item: item["case_id"]):
        now = time.monotonic()
        if previous_start is not None:
            remaining = min_start_interval_seconds - (now - previous_start)
            if remaining > 0:
                time.sleep(remaining)
        previous_start = time.monotonic()
        body = build_request_payload(arm=arm, query_text=row["query_text"])
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(
                body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                latency_ms = (time.perf_counter() - started) * 1000.0
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                error_payload = json.loads(raw)
                error = error_payload.get("error") or {}
                code = error.get("code", exc.code)
            except Exception:
                code = exc.code
            raise ProviderCallError(
                f"OPENROUTER_HTTP_{exc.code}_CODE_{code}"
            ) from None

        try:
            parsed = parse_response(arm=arm, payload=payload)
        except RuntimeError as exc:
            raise ProviderCallError(str(exc)) from None
        translated.append(
            {
                "case_id": row["case_id"],
                "query_text": row["query_text"],
                "translated_ru": parsed["translated_ru"],
                "target_content_fp": row["target_content_fp"],
                "latency_ms": latency_ms,
                "cost": parsed["cost"],
                "response_model": parsed["model"],
                "response_provider": parsed["provider"],
            }
        )
    return translated


def run_translation_attempts(
    *,
    en_rows,
    endpoint: str,
    translator=translate_rows,
):
    successes = {}
    failures = {}
    for arm in CANDIDATE_ARMS:
        try:
            successes[arm["name"]] = translator(
                arm=arm,
                en_rows=en_rows,
                endpoint=endpoint,
            )
        except ProviderCallError as exc:
            failures[arm["name"]] = str(exc)
    return successes, failures


def runtime_from_environment():
    database_url = os.environ.get("ISSUE174_DATABASE_URL")
    if database_url:
        return _AZURE.postgres_runtime(database_url)

    api_base = os.environ.get("ISSUE174_NEON_API_BASE")
    if not api_base:
        raise SystemExit(
            "ISSUE174_DATABASE_URL_OR_NEON_API_BASE_REQUIRED"
        )

    return _AZURE.postgres_runtime_from_neon_api(
        api_base=api_base,
        project_id=os.environ.get(
            "ISSUE174_NEON_PROJECT_ID", "wild-surf-61625738"
        ),
        branch_id=os.environ.get(
            "ISSUE174_NEON_BRANCH_ID", "br-sweet-dream-b5qzaztb"
        ),
        role_name=os.environ.get(
            "ISSUE174_NEON_ROLE", "neondb_owner"
        ),
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


def public_arm_receipt(*, arm, provider_rows, results, gold_digest_v2):
    ranks = {row["case_id"]: row["target_rank"] for row in results}
    digest_v2 = _AZURE.translation_digest(provider_rows)
    digest_historical = _AZURE.historical_translation_digest(provider_rows)

    receipt = _AZURE.public_receipt(
        provider_rows=provider_rows,
        ranks=ranks,
        gold_digest_v2=gold_digest_v2,
        translation_digest_v2=digest_v2,
        historical_translation_digest=digest_historical,
    )
    costs = [row.get("cost") for row in provider_rows]
    numeric_costs = [
        float(value)
        for value in costs
        if isinstance(value, (int, float))
    ]

    receipt.update(
        {
            "arm": arm["name"],
            "requested_model": arm["model"],
            "requested_provider": arm["provider"],
            "response_model": provider_rows[0]["response_model"],
            "response_provider": provider_rows[0]["response_provider"],
            "provider_cost_total": (
                sum(numeric_costs)
                if len(numeric_costs) == len(provider_rows)
                else None
            ),
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

    for arm in CANDIDATE_ARMS:
        if not schema_absent(runtime, arm["schema"]):
            raise SystemExit(
                f"ISSUE174_SCHEMA_ALREADY_EXISTS={arm['schema']}"
            )

    gold_rows = _AZURE._fetch_gold(runtime)
    gold_summary = _AZURE.validate_frozen_gold(
        gold_rows, expected_digest=EXPECTED_GOLD_DIGEST_V2
    )
    en_rows = [row for row in gold_rows if row["query_lang"] == "en"]

    endpoint = os.environ.get(
        "ISSUE174_OPENROUTER_ENDPOINT", DEFAULT_OPENROUTER_ENDPOINT
    )

    # Phase 1: attempt every provider arm before reading any retrieval result.
    translated, translation_failures = run_translation_attempts(
        en_rows=en_rows,
        endpoint=endpoint,
    )

    # Phase 2: freeze every successful translation set, still before retrieval.
    frozen = {}
    for arm in CANDIDATE_ARMS:
        provider_rows = translated.get(arm["name"])
        if provider_rows is None:
            continue
        _AZURE.EVAL_SCHEMA = arm["schema"]
        _AZURE._freeze_translations(runtime, provider_rows)
        readback_rows = _AZURE._readback_translation_rows(runtime)
        digest_v2 = _AZURE.translation_digest(provider_rows)
        if _AZURE.translation_digest(readback_rows) != digest_v2:
            raise RuntimeError(
                f"TRANSLATION_READBACK_DIGEST_MISMATCH arm={arm['name']}"
            )
        frozen[arm["name"]] = provider_rows

    # Phase 3: only after all attempts/freezes are done, inspect ranks.
    receipts = []
    for arm in CANDIDATE_ARMS:
        provider_rows = frozen.get(arm["name"])
        if provider_rows is None:
            continue
        _AZURE.EVAL_SCHEMA = arm["schema"]
        results = _AZURE._evaluate(runtime)
        receipts.append(
            public_arm_receipt(
                arm=arm,
                provider_rows=provider_rows,
                results=results,
                gold_digest_v2=gold_summary["gold_digest_v2"],
            )
        )

    print(
        json.dumps(
            {
                "gold": gold_summary,
                "arms": receipts,
                "translation_failures": translation_failures,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
