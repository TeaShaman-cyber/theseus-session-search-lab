"""Pure helpers for the frozen query-translation benchmark.

This module intentionally contains no provider, database, or credential code.
The live benchmark supplies translation rows and target ranks; these helpers
only canonicalize evidence and calculate deterministic acceptance metrics.
"""

from __future__ import annotations

import hashlib
import json
import math
from statistics import fmean
from typing import Iterable, Mapping, Sequence



def canonical_gold_payload(rows: Iterable[Mapping[str, str]]) -> str:
    ordered = sorted(
        (
            {
                "case_id": row["case_id"],
                "query_lang": row["query_lang"],
                "query_text": row["query_text"],
                "target_content_fp": row["target_content_fp"],
            }
            for row in rows
        ),
        key=lambda row: (row["case_id"], row["query_lang"]),
    )
    return json.dumps(
        ordered,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def gold_digest(rows: Iterable[Mapping[str, str]]) -> str:
    payload = canonical_gold_payload(rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_translation_payload(rows: Iterable[Mapping[str, str]]) -> str:
    ordered = sorted(
        (
            {
                "case_id": row["case_id"],
                "translated_ru": row["translated_ru"],
                "target_content_fp": row["target_content_fp"],
            }
            for row in rows
        ),
        key=lambda row: row["case_id"],
    )
    return json.dumps(
        ordered,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def translation_digest(rows: Iterable[Mapping[str, str]]) -> str:
    payload = canonical_translation_payload(rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def retrieval_metrics(ranks: Sequence[int | None]) -> dict[str, float | int]:
    count = len(ranks)
    if count == 0:
        raise ValueError("ranks must not be empty")

    def recall_at(k: int) -> float:
        return sum(rank is not None and rank <= k for rank in ranks) / count

    reciprocal_sum = sum(1.0 / rank for rank in ranks if rank is not None)
    return {
        "count": count,
        "r_at_1": recall_at(1),
        "r_at_3": recall_at(3),
        "r_at_5": recall_at(5),
        "r_at_10": recall_at(10),
        "mrr": reciprocal_sum / count,
    }


def _inclusive_percentile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("values must not be empty")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be between 0 and 1")

    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]

    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def latency_summary(samples_ms: Sequence[float]) -> dict[str, float | int]:
    if not samples_ms:
        raise ValueError("samples_ms must not be empty")
    values = [float(value) for value in samples_ms]
    return {
        "count": len(values),
        "p50_ms": _inclusive_percentile(values, 0.50),
        "p95_ms": _inclusive_percentile(values, 0.95),
        "mean_ms": fmean(values),
    }


def character_count(rows: Iterable[Mapping[str, str]]) -> int:
    return sum(len(row["query_text"]) for row in rows)
