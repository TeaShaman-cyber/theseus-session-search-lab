"""Fixed synthetic Top-K comparison; no DB/provider code and no production changes.

Compares event-row ranking with exact-text first-occurrence grouping. The
existing text score is supplied as input, not recomputed after grouping.
"""

from __future__ import annotations


def evaluate(events: list[dict], k: int = 10) -> dict:
    if k < 1:
        raise ValueError("k must be positive")

    # Same deterministic ordering as the Neon SQL baseline; never key by
    # message_id alone, which may repeat across branches with different text.
    ranked = sorted(
        events,
        key=lambda e: (-e["score"], e["session"], e["ordinal"], e["id"]),
    )
    baseline = ranked[:k]
    grouped: dict[str, dict] = {}
    for row in ranked:
        body = row["text"]  # exact equality, not fingerprint-only collision risk
        if body not in grouped:
            grouped[body] = {
                "text": body,
                "score": row["score"],
                "relevant": row["relevant"],
                "members": [],
            }
        elif grouped[body]["relevant"] != row["relevant"]:
            raise ValueError("gold relevance conflicts for identical text")
        grouped[body]["members"].append([row["session"], row["id"], row["ordinal"]])

    selected = list(grouped.values())[:k]

    return {
        "k": k,
        "total_events": len(events),
        "total_unique_texts": len(grouped),
        "baseline": {
            "event_rows": len(baseline),
            "unique_texts": len({e["text"] for e in baseline}),
            "unique_relevant_texts": len({e["text"] for e in baseline if e["relevant"]}),
        },
        "exact_grouped": {
            "unique_texts": len(selected),
            "unique_relevant_texts": sum(bool(g["relevant"]) for g in selected),
            "selected": selected,
        },
    }
