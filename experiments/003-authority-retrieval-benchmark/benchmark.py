#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_search.corpus_store import CorpusPaths, ingest_artifact, verify_corpus
from session_search.search import search_corpus

SCHEMA = "theseus.session-search-authority-retrieval-benchmark.v0"
SCOPES = ["dialogue", "evidence"]
LIMIT = 8


def _message(message_id: str, text: str, create_time: float, role: str = "assistant") -> dict:
    return {
        "id": message_id,
        "author": {"role": role},
        "create_time": create_time,
        "content": {"content_type": "text", "parts": [text]},
    }


def _write_capture(
    path: pathlib.Path,
    session_id: str,
    title: str,
    messages: list[dict],
) -> pathlib.Path:
    payload = {
        "conversation_id": session_id,
        "title": title,
        "page_info": {"has_previous_page": False, "has_next_page": False},
        "messages": messages,
    }
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    member = "optional/conversation-1.bin"
    manifest = {
        "schema": "barn-doctor-export:v1",
        "files": [
            {
                "name": member,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        zf.writestr(member, data)
    return path


def _session_order(rows: list[dict]) -> list[str]:
    result: list[str] = []
    for row in rows:
        session_id = str(row["session_id"])
        if session_id not in result:
            result.append(session_id)
    return result


def _rank(rows: list[dict], session_id: str | None) -> int | None:
    if session_id is None:
        return None
    for index, value in enumerate(_session_order(rows), 1):
        if value == session_id:
            return index
    return None


def _provenance_resolvable(corpus: pathlib.Path, rows: list[dict]) -> bool | None:
    if not rows:
        return None
    top = rows[0]
    with sqlite3.connect(CorpusPaths.from_root(corpus).db) as conn:
        count = conn.execute(
            """
            SELECT count(*)
            FROM messages m
            JOIN message_sources ms ON ms.message_row_id=m.row_id
            JOIN payload_pages p ON p.page_id=ms.page_id
            JOIN artifacts a ON a.artifact_id=p.artifact_id
            WHERE m.session_id=? AND m.message_id=?
            """,
            (top["session_id"], top["message_id"]),
        ).fetchone()[0]
    return bool(count)


def _run_case(root: pathlib.Path, case: dict) -> dict:
    case_root = root / case["id"]
    case_root.mkdir(parents=True)
    corpus = case_root / "corpus"
    for index, session in enumerate(case["sessions"], 1):
        capture = _write_capture(
            case_root / f"{index}.zip",
            session["session_id"],
            session["title"],
            [
                _message(
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

    strict = search_corpus(corpus, case["query"], SCOPES, LIMIT, recall=False)
    recall = search_corpus(corpus, case["query"], SCOPES, LIMIT, recall=True)
    intended = case.get("intended_session")
    strict_sessions = _session_order(strict)
    recall_sessions = _session_order(recall)

    observed = {
        "strict_result_count": len(strict),
        "recall_result_count": len(recall),
        "strict_sessions": strict_sessions,
        "recall_sessions": recall_sessions,
        "strict_target_rank": _rank(strict, intended),
        "recall_target_rank": _rank(recall, intended),
        "strict_unique_session_count": len(strict_sessions),
        "recall_unique_session_count": len(recall_sessions),
        "top_strict_provenance_resolvable": _provenance_resolvable(corpus, strict),
        "top_recall_provenance_resolvable": _provenance_resolvable(corpus, recall),
    }

    checks = {name: bool(check(observed)) for name, check in case["checks"].items()}
    return {
        "id": case["id"],
        "class": case["class"],
        "query": case["query"],
        "intended_session": intended,
        "authority_expectation": case["authority_expectation"],
        "baseline_disposition": case["baseline_disposition"],
        "observed": observed,
        "checks": checks,
        "pass": all(checks.values()),
    }


def _cases() -> list[dict]:
    return [
        {
            "id": "adjacent-wording-recall",
            "class": "LEXICAL_MISS_ADJACENT_WORDING",
            "query": "lightweight IDE",
            "intended_session": "adjacent-target",
            "authority_expectation": "recall may surface a candidate; lexical miss remains UNKNOWN",
            "baseline_disposition": "SUPPORTED_BY_CURRENT_RECALL",
            "sessions": [
                {
                    "session_id": "adjacent-target",
                    "title": "Development environment",
                    "messages": [
                        {"id": "a1", "role": "user", "text": "Could we add a small IDE layer?"},
                        {"id": "a2", "text": "The dev kit uses ruff shellcheck and shfmt."},
                    ],
                },
                {
                    "session_id": "adjacent-dump",
                    "title": "Reference dump",
                    "messages": [
                        {"id": "a3", "role": "tool", "text": "lightweight IDE reference output"}
                    ],
                },
            ],
            "checks": {
                "strict_misses_target": lambda o: o["strict_target_rank"] is None,
                "recall_surfaces_target": lambda o: o["recall_target_rank"] is not None,
            },
        },
        {
            "id": "split-across-messages",
            "class": "SESSION_LEVEL_CONCEPT_SPLIT",
            "query": "authority reconciliation",
            "intended_session": "split-target",
            "authority_expectation": "session aggregation may join lexical evidence across messages; messages remain separate evidence",
            "baseline_disposition": "SUPPORTED_BY_CURRENT_RECALL",
            "sessions": [
                {
                    "session_id": "split-target",
                    "title": "Library methods",
                    "messages": [
                        {"id": "s1", "text": "Authority control keeps concept identities explicit."},
                        {"id": "s2", "text": "Reconciliation maps mentions to controlled concepts."},
                    ],
                },
                {
                    "session_id": "split-noise",
                    "title": "Unrelated authority",
                    "messages": [{"id": "s3", "text": "Authority is granted by the operator."}],
                },
            ],
            "checks": {
                "strict_misses_split_session": lambda o: o["strict_target_rank"] is None,
                "recall_surfaces_split_session": lambda o: o["recall_target_rank"] is not None,
            },
        },
        {
            "id": "broad-context-candidate",
            "class": "BROAD_CONTEXT_FALSE_POSITIVE_RISK",
            "query": "resolvent Feshbach spectral measure",
            "intended_session": None,
            "authority_expectation": "broad OR result is CANDIDATE only and must not become evidence of the target concept",
            "baseline_disposition": "RISK_IF_DOWNSTREAM_PROMOTES_RECALL",
            "sessions": [
                {
                    "session_id": "context-only",
                    "title": "Spectral context",
                    "messages": [
                        {"id": "c1", "text": "The spectral measure describes the operator decomposition."},
                        {"id": "c2", "text": "Eigenvalue estimates are available."},
                    ],
                }
            ],
            "checks": {
                "strict_has_no_false_target": lambda o: o["strict_result_count"] == 0,
                "recall_exposes_context_candidate": lambda o: o["recall_result_count"] > 0,
            },
        },
        {
            "id": "alias-canonicalization-gap",
            "class": "ALIAS_CANONICALIZATION",
            "query": "retrieval augmented generation",
            "intended_session": "alias-target",
            "authority_expectation": "alias reconciliation may add a candidate but must preserve the original RAG mention and provenance",
            "baseline_disposition": "GAP_CANDIDATE_FOR_RECONCILIATION",
            "sessions": [
                {
                    "session_id": "alias-target",
                    "title": "RAG design",
                    "messages": [{"id": "g1", "text": "The RAG pipeline fetches evidence before composing an answer."}],
                }
            ],
            "checks": {
                "strict_misses_alias": lambda o: o["strict_target_rank"] is None,
                "recall_misses_alias": lambda o: o["recall_target_rank"] is None,
            },
        },
        {
            "id": "exact-positive-control",
            "class": "EXACT_LEXICAL_POSITIVE",
            "query": "immutable artifact provenance",
            "intended_session": "exact-target",
            "authority_expectation": "reconciliation must not degrade a strong exact lexical result",
            "baseline_disposition": "SUPPORTED_BY_STRICT",
            "sessions": [
                {
                    "session_id": "exact-target",
                    "title": "Evidence contract",
                    "messages": [{"id": "e1", "text": "Every edge keeps immutable artifact provenance."}],
                },
                {
                    "session_id": "exact-noise",
                    "title": "Artifact notes",
                    "messages": [{"id": "e2", "text": "An immutable artifact can be cached."}],
                },
            ],
            "checks": {
                "strict_target_is_rank_one": lambda o: o["strict_target_rank"] == 1,
                "strict_provenance_resolves": lambda o: o["top_strict_provenance_resolvable"] is True,
            },
        },
        {
            "id": "provenance-readback",
            "class": "PROVENANCE_RESOLUTION",
            "query": "source message evidence",
            "intended_session": "provenance-target",
            "authority_expectation": "retrieved message must remain resolvable through message_sources to accepted artifact provenance",
            "baseline_disposition": "SUPPORTED_BY_CORPUS_PROVENANCE",
            "sessions": [
                {
                    "session_id": "provenance-target",
                    "title": "Provenance",
                    "messages": [{"id": "p1", "text": "The source message evidence is retained exactly."}],
                }
            ],
            "checks": {
                "strict_finds_target": lambda o: o["strict_target_rank"] == 1,
                "strict_provenance_resolves": lambda o: o["top_strict_provenance_resolvable"] is True,
            },
        },
        {
            "id": "unresolved-ambiguity",
            "class": "AMBIGUOUS_CANONICALIZATION",
            "query": "Mercury",
            "intended_session": None,
            "authority_expectation": "multiple plausible concepts remain ambiguous; reconciliation must not silently choose one",
            "baseline_disposition": "GAP_NEEDS_EXPLICIT_AMBIGUITY_STATE",
            "sessions": [
                {
                    "session_id": "mercury-planet",
                    "title": "Astronomy",
                    "messages": [{"id": "m1", "text": "Mercury is the innermost planet."}],
                },
                {
                    "session_id": "mercury-language",
                    "title": "Programming",
                    "messages": [{"id": "m2", "text": "Mercury uses logic programming ideas."}],
                },
            ],
            "checks": {
                "strict_preserves_multiple_candidates": lambda o: o["strict_unique_session_count"] == 2,
                "recall_preserves_multiple_candidates": lambda o: o["recall_unique_session_count"] == 2,
            },
        },
    ]


def run_benchmark() -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        results = [_run_case(root, case) for case in _cases()]
    return {
        "schema": SCHEMA,
        "baseline": "session-search strict AND + opt-in recall OR/session rerank",
        "authority": "retrieval outputs are candidates; accepted artifacts/messages remain historical evidence authority",
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
