from __future__ import annotations

from collections.abc import Callable
from typing import Any


Shard = dict[str, Any]
ShardSearch = Callable[[Shard, str, list[str], int], dict[str, Any]]


def _matches_scope(
    shard: Shard,
    *,
    provider: str | None,
    session_id: str | None,
    from_time: float | None,
    to_time: float | None,
) -> bool:
    if provider is not None and shard.get("provider") != provider:
        return False

    if session_id is not None:
        known_sessions = shard.get("session_ids")
        if known_sessions is not None and session_id not in known_sessions:
            return False

    shard_min = shard.get("time_min")
    shard_max = shard.get("time_max")
    if from_time is not None and shard_max is not None and shard_max < from_time:
        return False
    if to_time is not None and shard_min is not None and shard_min > to_time:
        return False
    return True


def federated_search(
    catalog: list[Shard],
    search_shard: ShardSearch,
    query: str,
    scopes: list[str],
    limit: int,
    *,
    provider: str | None = None,
    session_id: str | None = None,
    from_time: float | None = None,
    to_time: float | None = None,
) -> dict[str, Any]:
    if not query.strip():
        raise ValueError("query contains no searchable text")
    if limit < 1 or limit > 100:
        raise ValueError("result limit out of bounds")

    relevant = sorted(
        (
            shard
            for shard in catalog
            if _matches_scope(
                shard,
                provider=provider,
                session_id=session_id,
                from_time=from_time,
                to_time=to_time,
            )
        ),
        key=lambda shard: shard["shard_id"],
    )

    unavailable: list[dict[str, str]] = []
    queried_shards: list[str] = []
    ranked_hits: list[tuple[float, str, dict[str, Any]]] = []

    for shard in relevant:
        shard_id = shard["shard_id"]
        expected_generation = shard["generation_id"]
        if shard.get("state") != "READY":
            unavailable.append({"shard_id": shard_id, "reason": "NOT_READY"})
            continue

        queried_shards.append(shard_id)
        try:
            outcome = search_shard(shard, query, scopes, limit)
        except Exception:
            unavailable.append({"shard_id": shard_id, "reason": "QUERY_FAILED"})
            continue

        if outcome.get("generation_id") != expected_generation:
            unavailable.append({"shard_id": shard_id, "reason": "STALE_GENERATION"})
            continue

        for shard_rank, original in enumerate(outcome.get("hits", []), start=1):
            hit = dict(original)
            hit["shard_id"] = shard_id
            hit["provider"] = shard.get("provider")
            hit["generation_id"] = expected_generation
            hit["shard_rank"] = shard_rank
            reciprocal_rank = 1.0 / (60 + shard_rank)
            ranked_hits.append((reciprocal_rank, shard_id, hit))

    ranked_hits.sort(
        key=lambda item: (
            -item[0],
            item[1],
            str(item[2].get("session_id", "")),
            int(item[2].get("ordinal", 0)),
            str(item[2].get("message_id", "")),
        )
    )
    hits = [item[2] for item in ranked_hits[:limit]]
    unavailable.sort(key=lambda item: item["shard_id"])

    if unavailable:
        status = "PARTIAL"
    elif hits:
        status = "FOUND"
    else:
        status = "NO_SIGNAL"

    return {
        "status": status,
        "hits": hits,
        "queried_shards": queried_shards,
        "unavailable_shards": unavailable,
    }
