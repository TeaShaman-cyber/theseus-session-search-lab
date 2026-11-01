#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_search.corpus_store import ingest_artifact, verify_corpus
from session_search.search import search_corpus

SCHEMA = "theseus.session-search-plugin-retrieval-policy-usability.v0"
LIMIT = 8
SCOPES = ["dialogue", "evidence"]
RUNNER = pathlib.Path(__file__).with_name("router_runner.mjs")
BASELINE_003 = ROOT / "experiments" / "003-authority-retrieval-benchmark" / "benchmark.py"


def _load_baseline_003():
    spec = importlib.util.spec_from_file_location("authority_retrieval_benchmark_003", BASELINE_003)
    if spec is None or spec.loader is None:
        raise RuntimeError("experiment 003 import failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B003 = _load_baseline_003()


def _case_from_003(source_id: str, *, case_id: str, class_name: str) -> dict:
    source = next(case for case in B003._cases() if case["id"] == source_id)
    case = copy.deepcopy(source)
    case["id"] = case_id
    case["class"] = class_name
    case.pop("checks", None)
    return case


def _cases() -> list[dict]:
    return [
        _case_from_003(
            "exact-positive-control",
            case_id="precise-lookup",
            class_name="PRECISE_LOOKUP",
        ),
        {
            "id": "overspecified-natural-language",
            "class": "OVERSPECIFIED_NATURAL_LANGUAGE",
            "query": "Session Search architecture CLI plugin App-backed interface retrieval completeness",
            "intended_session": "overspecified-target",
            "authority_expectation": (
                "strict lexical miss may escalate to recall; recall remains candidate retrieval"
            ),
            "baseline_disposition": "AUTO_SHOULD_ESCALATE_TO_RECALL",
            "sessions": [
                {
                    "session_id": "overspecified-target",
                    "title": "Session Search architecture",
                    "messages": [
                        {
                            "id": "o1",
                            "role": "user",
                            "text": "Session Search architecture uses the CLI for local diagnostics.",
                        },
                        {
                            "id": "o2",
                            "text": "The App-backed plugin exposes the retrieval interface.",
                        },
                    ],
                }
            ],
        },
        _case_from_003(
            "split-across-messages",
            case_id="split-across-messages",
            class_name="SESSION_LEVEL_CONCEPT_SPLIT",
        ),
        _case_from_003(
            "alias-canonicalization-gap",
            case_id="clean-miss-unknown",
            class_name="CLEAN_MISS_UNKNOWN",
        ),
    ]


def _build_corpus(root: pathlib.Path, case: dict) -> pathlib.Path:
    case_root = root / case["id"]
    case_root.mkdir(parents=True)
    corpus = case_root / "corpus"
    for index, session in enumerate(case["sessions"], 1):
        capture = B003._write_capture(
            case_root / f"{index}.zip",
            session["session_id"],
            session["title"],
            [
                B003._message(
                    message["id"],
                    message["text"],
                    float(message_index),
                    role=message.get("role", "assistant"),
                )
                for message_index, message in enumerate(session["messages"], 1)
            ],
        )
        ingest_artifact(capture, corpus)
    verification = verify_corpus(corpus)
    if verification.get("status") != "VERIFIED":
        raise RuntimeError(f"fixture corpus failed verification: {case['id']}: {verification}")
    return corpus


def _identity(rows: list[dict]) -> list[dict]:
    fields = ("session_id", "message_id", "ordinal", "search_class")
    return [{field: row.get(field) for field in fields} for row in rows]


def _sessions(rows: list[dict]) -> list[str]:
    result: list[str] = []
    for row in rows:
        session_id = str(row.get("session_id"))
        if session_id not in result:
            result.append(session_id)
    return result


def _rank(rows: list[dict], session_id: str | None) -> int | None:
    if session_id is None:
        return None
    for index, value in enumerate(_sessions(rows), 1):
        if value == session_id:
            return index
    return None


def _run_router(query: str, strict_rows: list[dict], recall_rows: list[dict]) -> dict:
    payload = {
        "query": query,
        "limit": LIMIT,
        "strict_hits": strict_rows,
        "recall_hits": recall_rows,
    }
    completed = subprocess.run(
        ["node", str(RUNNER)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=True,
        cwd=ROOT,
    )
    return json.loads(completed.stdout)


def _mode_summary(mode: dict) -> dict:
    result = mode["result"]
    return {
        "status": result["status"],
        "identities": _identity(result.get("hits", [])),
        "sessions": _sessions(result.get("hits", [])),
        "queried_shards": result.get("queried_shards", []),
        "unavailable_shards": result.get("unavailable_shards", []),
        "retrieval": result.get("retrieval"),
        "dispatch_modes": mode["dispatch_modes"],
    }


def _run_case(root: pathlib.Path, case: dict) -> dict:
    corpus = _build_corpus(root, case)
    strict = search_corpus(corpus, case["query"], SCOPES, LIMIT, recall=False)
    recall = search_corpus(corpus, case["query"], SCOPES, LIMIT, recall=True)
    router = _run_router(case["query"], strict, recall)

    plugin_strict = router["strict"]["result"]
    plugin_recall = router["recall"]["result"]
    plugin_auto = router["auto"]["result"]
    expected_auto = strict if strict else recall
    expected_effective = "strict" if strict else "recall"
    expected_dispatch = ["strict"] if strict else ["strict", "recall"]
    intended = case.get("intended_session")

    receipt = plugin_auto.get("retrieval") or {}
    checks = {
        "plugin_strict_matches_cli_strict": _identity(plugin_strict.get("hits", [])) == _identity(strict),
        "plugin_recall_matches_cli_recall": _identity(plugin_recall.get("hits", [])) == _identity(recall),
        "auto_matches_bounded_policy": (
            _identity(plugin_auto.get("hits", [])) == _identity(expected_auto)
            and receipt.get("effective_mode") == expected_effective
            and router["auto"]["dispatch_modes"] == expected_dispatch
        ),
        "receipt_matches_policy": (
            receipt.get("requested_mode") == "auto"
            and receipt.get("strict_hit_count") == len(strict)
            and receipt.get("candidate_count") == len(expected_auto)
            and receipt.get("returned_hit_count") == len(expected_auto)
        ),
        "all_shards_available": (
            plugin_strict.get("unavailable_shards") == []
            and plugin_recall.get("unavailable_shards") == []
            and plugin_auto.get("unavailable_shards") == []
        ),
    }

    if case["class"] == "PRECISE_LOOKUP":
        behavior = {
            "target_is_strict_rank_one": _rank(strict, intended) == 1,
            "auto_stays_strict": receipt.get("effective_mode") == "strict",
        }
    elif case["class"] in {"OVERSPECIFIED_NATURAL_LANGUAGE", "SESSION_LEVEL_CONCEPT_SPLIT"}:
        behavior = {
            "strict_misses_target": _rank(strict, intended) is None,
            "recall_surfaces_target": _rank(recall, intended) is not None,
            "auto_escalates_to_recall": receipt.get("effective_mode") == "recall",
        }
    elif case["class"] == "CLEAN_MISS_UNKNOWN":
        behavior = {
            "strict_is_empty": strict == [],
            "recall_is_empty": recall == [],
            "auto_remains_no_signal": (
                plugin_auto.get("status") == "NO_SIGNAL"
                and plugin_auto.get("hits") == []
                and receipt.get("effective_mode") == "recall"
            ),
        }
    else:
        raise RuntimeError(f"unexpected benchmark class: {case['class']}")

    plugin = {
        "strict": _mode_summary(router["strict"]),
        "recall": _mode_summary(router["recall"]),
        "auto": _mode_summary(router["auto"]),
        "unavailable_shards": plugin_auto.get("unavailable_shards", []),
    }
    cli = {
        "strict_result_count": len(strict),
        "recall_result_count": len(recall),
        "strict_sessions": _sessions(strict),
        "recall_sessions": _sessions(recall),
        "strict_target_rank": _rank(strict, intended),
        "recall_target_rank": _rank(recall, intended),
    }
    all_checks = {**checks, **behavior}
    return {
        "id": case["id"],
        "class": case["class"],
        "query": case["query"],
        "intended_session": intended,
        "authority_expectation": case["authority_expectation"],
        "baseline_disposition": case["baseline_disposition"],
        "cli": cli,
        "plugin": plugin,
        "checks": all_checks,
        "pass": all(all_checks.values()),
    }


def run_benchmark() -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        results = [_run_case(root, case) for case in _cases()]
    return {
        "schema": SCHEMA,
        "comparison": "local CLI search_corpus strict/recall vs plugin-facing MCP router strict/recall/auto",
        "authority": (
            "retrieval output remains candidate evidence; benchmark proves retrieval-policy parity only"
        ),
        "case_count": len(results),
        "pass_count": sum(result["pass"] for result in results),
        "cases": results,
        "overall": "PASS" if all(result["pass"] for result in results) else "FAIL",
    }


def main() -> int:
    print(json.dumps(run_benchmark(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
