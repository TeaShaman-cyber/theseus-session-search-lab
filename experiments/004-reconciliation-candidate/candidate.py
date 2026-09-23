#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_search.corpus_store import ingest_artifact, verify_corpus
from session_search.search import query_tokens, search_corpus

SCHEMA = "theseus.session-search-reconciliation-comparison.v0"
BASELINE_PATH = ROOT / "experiments" / "003-authority-retrieval-benchmark" / "baseline.public.json"
BASELINE_MODULE_PATH = ROOT / "experiments" / "003-authority-retrieval-benchmark" / "benchmark.py"
REGISTRY_PATH = pathlib.Path(__file__).with_name("concepts.public.json")
SCOPES = ["dialogue", "evidence"]
LIMIT = 8


def _load_baseline_module():
    spec = importlib.util.spec_from_file_location("authority_retrieval_baseline", BASELINE_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("baseline module import failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized(text: str) -> str:
    return " ".join(query_tokens(text))


def _load_registry() -> dict[str, Any]:
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if payload.get("schema") != "theseus.session-search-controlled-concepts.v0":
        raise ValueError("CONCEPT_REGISTRY_SCHEMA_UNSUPPORTED")
    concepts = payload.get("concepts")
    if not isinstance(concepts, list):
        raise ValueError("CONCEPT_REGISTRY_INVALID")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in concepts:
        if not isinstance(raw, dict):
            raise ValueError("CONCEPT_INVALID")
        concept_id = raw.get("id")
        preferred = raw.get("preferred_label")
        aliases = raw.get("aliases")
        if not isinstance(concept_id, str) or not concept_id or concept_id in seen:
            raise ValueError("CONCEPT_ID_INVALID")
        if not isinstance(preferred, str) or not preferred.strip():
            raise ValueError("CONCEPT_LABEL_INVALID")
        if not isinstance(aliases, list) or not all(isinstance(x, str) and x.strip() for x in aliases):
            raise ValueError("CONCEPT_ALIASES_INVALID")
        seen.add(concept_id)
        forms = [preferred.strip(), *[x.strip() for x in aliases]]
        normalized.append(
            {
                "id": concept_id,
                "preferred_label": preferred.strip(),
                "aliases": [x.strip() for x in aliases],
                "forms": forms,
                "normalized_forms": [_normalized(x) for x in forms],
            }
        )
    return {"schema": payload["schema"], "concepts": normalized}


def _matching_concepts(query: str, registry: dict[str, Any]) -> list[dict[str, Any]]:
    needle = _normalized(query)
    return [concept for concept in registry["concepts"] if needle in concept["normalized_forms"]]


def _row_key(row: dict) -> tuple[str, str, int]:
    return str(row["session_id"]), str(row["message_id"]), int(row["ordinal"])


def _merge_rows(*groups: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for group in groups:
        for row in group:
            key = _row_key(row)
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
    return result[:LIMIT]


def _typed_rows(
    rows: list[dict], *, basis: str, promotion_eligibility: str
) -> list[dict]:
    return [
        {
            **row,
            "retrieval_basis": basis,
            "promotion_eligibility": promotion_eligibility,
            "authority": "NON_AUTHORITATIVE_RETRIEVAL",
        }
        for row in rows
    ]


def _merge_typed_rows(*groups: list[dict]) -> list[dict]:
    return _merge_rows(*groups)


def _summary_eligibility(rows: list[dict]) -> str:
    values = {str(row["promotion_eligibility"]) for row in rows}
    if not values:
        return "UNKNOWN"
    if len(values) == 1:
        return next(iter(values))
    return "MIXED"


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


def reconcile_search(corpus: pathlib.Path, query: str, registry: dict[str, Any]) -> dict[str, Any]:
    strict = search_corpus(corpus, query, SCOPES, LIMIT, recall=False)
    recall = search_corpus(corpus, query, SCOPES, LIMIT, recall=True)
    strict_keys = {_row_key(row) for row in strict}
    recall_only = [row for row in recall if _row_key(row) not in strict_keys]
    concept_matches = _matching_concepts(query, registry)

    if len(concept_matches) > 1:
        rows = _merge_typed_rows(
            _typed_rows(strict, basis="AMBIGUOUS_DIRECT_LEXICAL", promotion_eligibility="AMBIGUOUS"),
            _typed_rows(recall_only, basis="AMBIGUOUS_RECALL", promotion_eligibility="AMBIGUOUS"),
        )
        return {
            "state": "AMBIGUOUS_CONCEPT",
            "promotion_eligibility": "AMBIGUOUS",
            "authority": "NON_AUTHORITATIVE_RETRIEVAL",
            "query_mode": "controlled_alias_collision",
            "concept_matches": [
                {"id": c["id"], "preferred_label": c["preferred_label"]} for c in concept_matches
            ],
            "expanded_forms": [],
            "rows": rows,
        }

    if len(concept_matches) == 1:
        concept = concept_matches[0]
        query_norm = _normalized(query)
        expanded_forms = [form for form in concept["forms"] if _normalized(form) != query_norm]
        expanded_rows: list[dict] = []
        for form in expanded_forms:
            expanded_rows = _merge_rows(
                expanded_rows,
                search_corpus(corpus, form, SCOPES, LIMIT, recall=False),
            )
        expanded_keys = {_row_key(row) for row in expanded_rows}
        if expanded_rows:
            strict_without_expanded = [row for row in strict if _row_key(row) not in expanded_keys]
            supplemental_recall = [
                row
                for row in recall_only
                if _row_key(row) not in expanded_keys
            ]
            rows = _merge_typed_rows(
                _typed_rows(
                    expanded_rows,
                    basis="CONTROLLED_ALIAS_EXPANSION",
                    promotion_eligibility="CANDIDATE_ONLY",
                ),
                _typed_rows(
                    strict_without_expanded,
                    basis="DIRECT_LEXICAL",
                    promotion_eligibility="LEXICAL_EVIDENCE_ELIGIBLE",
                ),
                _typed_rows(
                    supplemental_recall,
                    basis="RECALL_ONLY",
                    promotion_eligibility="CANDIDATE_ONLY",
                ),
            )
            return {
                "state": "RECONCILED_CANDIDATE",
                "promotion_eligibility": _summary_eligibility(rows),
                "authority": "NON_AUTHORITATIVE_RETRIEVAL",
                "query_mode": "controlled_alias_expansion",
                "concept_matches": [
                    {"id": concept["id"], "preferred_label": concept["preferred_label"]}
                ],
                "expanded_forms": expanded_forms,
                "rows": rows,
            }

    if strict:
        rows = _merge_typed_rows(
            _typed_rows(
                strict,
                basis="DIRECT_LEXICAL",
                promotion_eligibility="LEXICAL_EVIDENCE_ELIGIBLE",
            ),
            _typed_rows(
                recall_only,
                basis="RECALL_ONLY",
                promotion_eligibility="CANDIDATE_ONLY",
            ),
        )
        return {
            "state": "DIRECT_WITH_RECALL_CANDIDATES" if recall_only else "DIRECT_LEXICAL",
            "promotion_eligibility": _summary_eligibility(rows),
            "authority": "NON_AUTHORITATIVE_RETRIEVAL",
            "query_mode": "strict_and_with_recall_candidates",
            "concept_matches": [],
            "expanded_forms": [],
            "rows": rows,
        }
    if recall:
        rows = _typed_rows(
            recall, basis="RECALL_ONLY", promotion_eligibility="CANDIDATE_ONLY"
        )
        return {
            "state": "RECALL_CANDIDATE",
            "promotion_eligibility": "CANDIDATE_ONLY",
            "authority": "NON_AUTHORITATIVE_RETRIEVAL",
            "query_mode": "explicit_recall_or",
            "concept_matches": [],
            "expanded_forms": [],
            "rows": rows,
        }
    return {
        "state": "UNKNOWN",
        "promotion_eligibility": "UNKNOWN",
        "authority": "NON_AUTHORITATIVE_RETRIEVAL",
        "query_mode": "no_candidate",
        "concept_matches": [],
        "expanded_forms": [],
        "rows": [],
    }


def _build_case_corpus(root: pathlib.Path, case: dict, baseline: Any) -> pathlib.Path:
    case_root = root / case["id"]
    case_root.mkdir(parents=True)
    corpus = case_root / "corpus"
    for index, session in enumerate(case["sessions"], 1):
        capture = baseline._write_capture(
            case_root / f"{index}.zip",
            session["session_id"],
            session["title"],
            [
                baseline._message(
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


def _provenance_resolvable(corpus: pathlib.Path, rows: list[dict], baseline: Any) -> bool | None:
    return baseline._provenance_resolvable(corpus, rows)


def run_comparison() -> dict[str, Any]:
    baseline = _load_baseline_module()
    frozen = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    current_baseline = baseline.run_benchmark()
    if current_baseline != frozen:
        raise RuntimeError("FROZEN_BASELINE_DRIFT")
    registry = _load_registry()

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        for case in baseline._cases():
            corpus = _build_case_corpus(root, case, baseline)
            candidate = reconcile_search(corpus, case["query"], registry)
            rows = candidate.pop("rows")
            candidate.update(
                {
                    "sessions": _session_order(rows),
                    "target_rank": _rank(rows, case.get("intended_session")),
                    "result_count": len(rows),
                    "top_provenance_resolvable": _provenance_resolvable(corpus, rows, baseline),
                    "hits": [
                        {
                            "session_id": row["session_id"],
                            "message_id": row["message_id"],
                            "retrieval_basis": row["retrieval_basis"],
                            "promotion_eligibility": row["promotion_eligibility"],
                        }
                        for row in rows
                    ],
                }
            )

            case_id = case["id"]
            checks: dict[str, bool]
            if case_id == "adjacent-wording-recall":
                checks = {
                    "target_remains_visible": candidate["target_rank"] is not None,
                    "no_reconciliation_needed": candidate["state"] == "DIRECT_WITH_RECALL_CANDIDATES",
                    "recall_target_stays_candidate_only": any(
                        hit["session_id"] == "adjacent-target"
                        and hit["promotion_eligibility"] == "CANDIDATE_ONLY"
                        for hit in candidate["hits"]
                    ),
                }
            elif case_id == "split-across-messages":
                checks = {
                    "target_remains_visible": candidate["target_rank"] == 1,
                    "remains_candidate_only": candidate["promotion_eligibility"] == "CANDIDATE_ONLY",
                }
            elif case_id == "broad-context-candidate":
                checks = {
                    "broad_context_is_recall_candidate": candidate["state"] == "RECALL_CANDIDATE",
                    "broad_context_not_promotable": candidate["promotion_eligibility"] == "CANDIDATE_ONLY",
                }
            elif case_id == "alias-canonicalization-gap":
                checks = {
                    "alias_gap_is_recovered": candidate["state"] == "RECONCILED_CANDIDATE"
                    and candidate["target_rank"] == 1,
                    "alias_hit_remains_candidate_only": candidate["promotion_eligibility"] == "CANDIDATE_ONLY",
                }
            elif case_id == "exact-positive-control":
                checks = {
                    "exact_rank_not_degraded": candidate["state"] == "DIRECT_WITH_RECALL_CANDIDATES"
                    and candidate["target_rank"] == 1,
                    "direct_target_is_typed": candidate["hits"][0]["promotion_eligibility"]
                    == "LEXICAL_EVIDENCE_ELIGIBLE",
                    "supplemental_recall_not_promoted": all(
                        hit["promotion_eligibility"] == "CANDIDATE_ONLY"
                        for hit in candidate["hits"]
                        if hit["retrieval_basis"] == "RECALL_ONLY"
                    ),
                }
            elif case_id == "provenance-readback":
                checks = {
                    "provenance_remains_resolvable": candidate["top_provenance_resolvable"] is True,
                    "direct_query_remains_rank_one": candidate["target_rank"] == 1,
                }
            elif case_id == "unresolved-ambiguity":
                checks = {
                    "ambiguity_is_explicit": candidate["state"] == "AMBIGUOUS_CONCEPT"
                    and len(candidate["concept_matches"]) == 2,
                    "ambiguity_is_not_promotable": candidate["promotion_eligibility"] == "AMBIGUOUS",
                    "both_candidates_remain_visible": len(candidate["sessions"]) == 2,
                }
            else:
                raise RuntimeError(f"UNREGISTERED_BENCHMARK_CASE: {case_id}")

            results.append(
                {
                    "id": case_id,
                    "baseline_disposition": case["baseline_disposition"],
                    "candidate": candidate,
                    "checks": checks,
                    "pass": all(checks.values()),
                }
            )

    all_pass = all(case["pass"] for case in results)
    return {
        "schema": SCHEMA,
        "frozen_baseline_sha256": _sha256(BASELINE_PATH),
        "concept_registry_sha256": _sha256(REGISTRY_PATH),
        "candidate": "exact controlled-label/alias reconciliation + explicit ambiguity state",
        "new_runtime_dependencies": [],
        "persisted_derived_state": False,
        "authority": "all candidate/reconciliation states remain NON_AUTHORITATIVE_RETRIEVAL",
        "case_count": len(results),
        "pass_count": sum(case["pass"] for case in results),
        "cases": results,
        "overall": "PASS" if all_pass else "FAIL",
        "research_signal": "KEEP_MINIMAL_LOCAL_RECONCILIATION" if all_pass else "INCONCLUSIVE",
    }


def main() -> int:
    print(json.dumps(run_comparison(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
