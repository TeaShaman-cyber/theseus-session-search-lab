from __future__ import annotations

import math
import re
from typing import Any

_EN_RE = re.compile(r"^[a-z]+$")
_DIGIT_RE = re.compile(r"\d")

_RETRIEVAL_GLUE = {
    # Russian query framing, expressed as analyzer-normalized lemmas.
    "найти",
    "искать",
    "поиск",
    "сообщение",
    "разговор",
    "обсуждать",
    "говорить",
    "писать",
    "упоминать",
    "помнить",
    "вспомнить",
    "чат",
    "ветка",
    # English query framing.
    "find",
    "search",
    "message",
    "messages",
    "conversation",
    "discuss",
    "talk",
    "write",
    "mention",
    "remember",
    "chat",
    "thread",
}

_EN_CLOSED_CLASS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "his", "i", "if",
    "in", "into", "is", "it", "its", "me", "my", "no", "not", "of",
    "on", "or", "our", "she", "so", "that", "the", "their", "them",
    "there", "they", "this", "to", "was", "we", "were", "what", "when",
    "where", "which", "who", "why", "with", "without", "you", "your",
}

_GLUE_POS = {"PREP", "CONJ", "PRCL", "INTJ", "NPRO", "PRED"}
_QUALIFIER_POS = {"ADJF", "ADJS", "PRTF", "PRTS", "NUMR"}
_NAMED_GRAMMEMES = {"Name", "Surn", "Patr", "Geox", "Orgn", "Trad"}

_PRIORITY = {
    "identifier": 5,
    "technical": 4,
    "nominal": 3,
    "qualifier": 2,
    "other": 1,
    "glue": 0,
}


def classify_token(
    surface: str,
    normalized: str,
    *,
    pos: str | None = None,
    grammemes: set[str] | frozenset[str] = frozenset(),
) -> str:
    if not isinstance(surface, str) or not surface:
        raise ValueError("QUERY_PLANNER_TOKEN_INVALID")
    if not isinstance(normalized, str) or not normalized:
        raise ValueError("QUERY_PLANNER_TOKEN_INVALID")

    if (
        normalized in _RETRIEVAL_GLUE
        or surface.casefold() in _RETRIEVAL_GLUE
        or normalized in _EN_CLOSED_CLASS
        or surface.casefold() in _EN_CLOSED_CLASS
    ):
        return "glue"
    if _DIGIT_RE.search(surface):
        return "identifier"
    if pos in _GLUE_POS:
        return "glue"
    if _EN_RE.fullmatch(normalized):
        return "technical"
    if pos == "NOUN" or _NAMED_GRAMMEMES.intersection(grammemes):
        return "nominal"
    if pos in _QUALIFIER_POS:
        return "qualifier"
    return "other"


def _validate_feature(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("QUERY_PLANNER_FEATURE_INVALID")
    token = value.get("token")
    ordinal = value.get("ordinal")
    idf = value.get("idf")
    kind = value.get("kind")
    if not isinstance(token, str) or not token:
        raise ValueError("QUERY_PLANNER_FEATURE_INVALID")
    if not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("QUERY_PLANNER_FEATURE_INVALID")
    if not isinstance(idf, (int, float)) or not math.isfinite(float(idf)):
        raise ValueError("QUERY_PLANNER_FEATURE_INVALID")
    if kind not in _PRIORITY:
        raise ValueError("QUERY_PLANNER_FEATURE_INVALID")
    return {
        "token": token,
        "ordinal": ordinal,
        "idf": float(idf),
        "kind": kind,
    }


def plan_features(features: list[dict[str, Any]], *, budget: int = 5) -> dict[str, Any]:
    if not isinstance(budget, int) or budget < 1 or budget > 32:
        raise ValueError("QUERY_PLANNER_BUDGET_INVALID")
    if not isinstance(features, list):
        raise ValueError("QUERY_PLANNER_FEATURES_INVALID")

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in features:
        feature = _validate_feature(raw)
        if feature["token"] in seen:
            continue
        seen.add(feature["token"])
        unique.append(feature)

    content = [row for row in unique if row["kind"] != "glue"]
    fallback = not content
    pool = unique if fallback else content

    ranked = sorted(
        pool,
        key=lambda row: (
            -_PRIORITY[row["kind"]],
            -row["idf"],
            row["ordinal"],
            row["token"],
        ),
    )
    selected_tokens = {row["token"] for row in ranked[:budget]}
    tokens = [
        row["token"]
        for row in sorted(unique, key=lambda row: (row["ordinal"], row["token"]))
        if row["token"] in selected_tokens
    ]

    return {
        "tokens": tokens,
        "fallback": fallback,
        "budget": budget,
        "input_unique_tokens": len(unique),
        "selected_tokens": len(tokens),
    }
