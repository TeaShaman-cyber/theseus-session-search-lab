from __future__ import annotations

import json
import pathlib
from typing import Any

REGISTRY_SCHEMA = "theseus.session-search-controlled-concepts.v0"
RESULT_SCHEMA = "theseus.session-search-reconciliation-result.v1"


def _normalized(text: str) -> str:
    from .search import query_tokens

    return " ".join(query_tokens(text))


def load_concept_registry(path: pathlib.Path | str) -> dict[str, Any]:
    path = pathlib.Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"CONCEPT_REGISTRY_UNREADABLE: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != REGISTRY_SCHEMA:
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
        if not isinstance(concept_id, str) or not concept_id.strip():
            raise ValueError("CONCEPT_ID_INVALID")
        concept_id = concept_id.strip()
        if concept_id in seen:
            raise ValueError("CONCEPT_ID_INVALID")
        if not isinstance(preferred, str) or not preferred.strip():
            raise ValueError("CONCEPT_LABEL_INVALID")
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) and alias.strip() for alias in aliases
        ):
            raise ValueError("CONCEPT_ALIASES_INVALID")
        seen.add(concept_id)
        forms = [preferred.strip(), *[alias.strip() for alias in aliases]]
        normalized_forms = [_normalized(form) for form in forms]
        if any(not form for form in normalized_forms):
            raise ValueError("CONCEPT_FORM_UNSEARCHABLE")
        normalized.append(
            {
                "id": concept_id,
                "preferred_label": preferred.strip(),
                "aliases": [alias.strip() for alias in aliases],
                "forms": forms,
                "normalized_forms": normalized_forms,
            }
        )
    return {"schema": REGISTRY_SCHEMA, "concepts": normalized}


def _matching_concepts(query: str, registry: dict[str, Any]) -> list[dict[str, Any]]:
    needle = _normalized(query)
    return [
        concept
        for concept in registry["concepts"]
        if needle in concept["normalized_forms"]
    ]


def _row_key(row: dict) -> tuple[str, str, int]:
    return str(row["session_id"]), str(row["message_id"]), int(row["ordinal"])


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


def _merge_rows(*groups: list[dict], limit: int) -> list[dict]:
    if limit <= 0:
        return []
    result: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for group in groups:
        for row in group:
            key = _row_key(row)
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
            if len(result) >= limit:
                return result
    return result


def _summary_eligibility(rows: list[dict]) -> str:
    values = {str(row["promotion_eligibility"]) for row in rows}
    if not values:
        return "UNKNOWN"
    if len(values) == 1:
        return next(iter(values))
    return "MIXED"


def _result(
    *,
    query: str,
    state: str,
    query_mode: str,
    rows: list[dict],
    concept_matches: list[dict[str, str]],
    expanded_forms: list[str],
    promotion_eligibility: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "query": query,
        "state": state,
        "promotion_eligibility": (
            promotion_eligibility
            if promotion_eligibility is not None
            else _summary_eligibility(rows)
        ),
        "authority": "NON_AUTHORITATIVE_RETRIEVAL",
        "query_mode": query_mode,
        "concept_matches": concept_matches,
        "expanded_forms": expanded_forms,
        "hits": rows,
    }


def reconcile_search(
    corpus_root: pathlib.Path,
    query: str,
    scopes: list[str],
    limit: int,
    registry: dict[str, Any],
    *,
    session_id: str | None = None,
    recall: bool = False,
) -> dict[str, Any]:
    from .search import search_corpus

    strict = search_corpus(
        corpus_root,
        query,
        scopes,
        limit,
        session_id=session_id,
        recall=False,
    )
    recalled = (
        search_corpus(
            corpus_root,
            query,
            scopes,
            limit,
            session_id=session_id,
            recall=True,
        )
        if recall
        else []
    )
    strict_keys = {_row_key(row) for row in strict}
    recall_only = [row for row in recalled if _row_key(row) not in strict_keys]
    concept_matches = _matching_concepts(query, registry)

    if len(concept_matches) > 1:
        rows = _merge_rows(
            _typed_rows(
                strict,
                basis="AMBIGUOUS_DIRECT_LEXICAL",
                promotion_eligibility="AMBIGUOUS",
            ),
            _typed_rows(
                recall_only,
                basis="AMBIGUOUS_RECALL",
                promotion_eligibility="AMBIGUOUS",
            ),
            limit=limit,
        )
        return _result(
            query=query,
            state="AMBIGUOUS_CONCEPT",
            query_mode="controlled_alias_collision",
            rows=rows,
            concept_matches=[
                {"id": concept["id"], "preferred_label": concept["preferred_label"]}
                for concept in concept_matches
            ],
            expanded_forms=[],
            promotion_eligibility="AMBIGUOUS",
        )

    if strict:
        rows = _merge_rows(
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
            limit=limit,
        )
        return _result(
            query=query,
            state="DIRECT_WITH_RECALL_CANDIDATES" if recall_only else "DIRECT_LEXICAL",
            query_mode="strict_and_with_recall_candidates" if recall else "strict_and",
            rows=rows,
            concept_matches=[
                {"id": concept["id"], "preferred_label": concept["preferred_label"]}
                for concept in concept_matches
            ],
            expanded_forms=[],
        )

    if len(concept_matches) == 1:
        concept = concept_matches[0]
        query_norm = _normalized(query)
        expanded_forms = [
            form for form in concept["forms"] if _normalized(form) != query_norm
        ]
        expanded: list[dict] = []
        for form in expanded_forms:
            expanded = _merge_rows(
                expanded,
                search_corpus(
                    corpus_root,
                    form,
                    scopes,
                    limit,
                    session_id=session_id,
                    recall=False,
                ),
                limit=limit,
            )
        if expanded:
            expanded_keys = {_row_key(row) for row in expanded}
            supplemental_recall = [
                row for row in recall_only if _row_key(row) not in expanded_keys
            ]
            rows = _merge_rows(
                _typed_rows(
                    expanded,
                    basis="CONTROLLED_ALIAS_EXPANSION",
                    promotion_eligibility="CANDIDATE_ONLY",
                ),
                _typed_rows(
                    supplemental_recall,
                    basis="RECALL_ONLY",
                    promotion_eligibility="CANDIDATE_ONLY",
                ),
                limit=limit,
            )
            return _result(
                query=query,
                state="RECONCILED_CANDIDATE",
                query_mode="controlled_alias_expansion",
                rows=rows,
                concept_matches=[
                    {"id": concept["id"], "preferred_label": concept["preferred_label"]}
                ],
                expanded_forms=expanded_forms,
            )

    if recall_only:
        rows = _typed_rows(
            recall_only,
            basis="RECALL_ONLY",
            promotion_eligibility="CANDIDATE_ONLY",
        )[:limit]
        return _result(
            query=query,
            state="RECALL_CANDIDATE",
            query_mode="explicit_recall_or",
            rows=rows,
            concept_matches=[],
            expanded_forms=[],
        )

    return _result(
        query=query,
        state="UNKNOWN",
        query_mode="no_candidate",
        rows=[],
        concept_matches=[
            {"id": concept["id"], "preferred_label": concept["preferred_label"]}
            for concept in concept_matches
        ],
        expanded_forms=[],
        promotion_eligibility="UNKNOWN",
    )
