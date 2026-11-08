from __future__ import annotations

from typing import Any

_THRESHOLD = 0.5
_VALID_KINDS = {
    "identifier",
    "technical",
    "nominal",
    "qualifier",
    "other",
    "glue",
}


def select_view(features: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(features, list):
        raise ValueError("SELECTOR_FEATURES_INVALID")

    unique: dict[str, str] = {}
    for raw in features:
        if not isinstance(raw, dict):
            raise ValueError("SELECTOR_FEATURE_INVALID")
        token = raw.get("token")
        kind = raw.get("kind")
        if (
            not isinstance(token, str)
            or not token
            or kind not in _VALID_KINDS
        ):
            raise ValueError("SELECTOR_FEATURE_INVALID")
        unique.setdefault(token, kind)

    total = len(unique)
    if total == 0:
        return {
            "mode": "full",
            "glue_ratio": 0.0,
            "glue_tokens": 0,
            "unique_tokens": 0,
        }

    glue = sum(kind == "glue" for kind in unique.values())
    ratio = glue / total
    return {
        "mode": "sparse" if ratio >= _THRESHOLD else "full",
        "glue_ratio": ratio,
        "glue_tokens": glue,
        "unique_tokens": total,
    }
