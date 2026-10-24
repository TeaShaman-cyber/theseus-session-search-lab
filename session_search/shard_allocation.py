from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


# Current explicit adapter -> provider lineage. Unknown adapters remain unresolved
# unless they are co-routed with exactly one known provider.
ADAPTER_PROVIDERS: dict[str, str] = {
    "chatgpt-export": "chatgpt",
    "barn-doctor": "chatgpt",
    "barn-doctor-recovery": "chatgpt",
    "speed-booster-export": "chatgpt",
    "deepseek-export": "deepseek",
    "xai-export": "xai",
}

SEARCHABLE_CLASSES = ("dialogue", "evidence", "trace")
ALLOCATION_CONTRACT_VERSION = "provider-aware-v1"

# Fitted from issue #61 / PR #74 synthetic Postgres sizing.
FIXED_OVERHEAD_BYTES = 4_648_141
PER_MESSAGE_NUMERATOR = 97_897
PER_MESSAGE_DENOMINATOR = 10
PER_TEXT_BYTE_NUMERATOR = 55_839
PER_TEXT_BYTE_DENOMINATOR = 10_000

# Working target from #75; official Neon Free hard project limit is decimal 1 GB.
DEFAULT_TARGET_BYTES = 700 * 1024 * 1024
HARD_LIMIT_BYTES = 1_000_000_000


def _ceil_ratio(value: int, numerator: int, denominator: int) -> int:
    if value < 0:
        raise ValueError("NEGATIVE_SIZE_INPUT")
    return (value * numerator + denominator - 1) // denominator


def estimate_session_bytes(searchable_messages: int, searchable_text_bytes: int) -> int:
    if searchable_messages < 0 or searchable_text_bytes < 0:
        raise ValueError("NEGATIVE_SIZE_INPUT")
    by_messages = _ceil_ratio(
        searchable_messages, PER_MESSAGE_NUMERATOR, PER_MESSAGE_DENOMINATOR
    )
    by_text = _ceil_ratio(
        searchable_text_bytes, PER_TEXT_BYTE_NUMERATOR, PER_TEXT_BYTE_DENOMINATOR
    )
    return max(by_messages, by_text)


def provider_from_adapters(adapters: Iterable[str]) -> str | None:
    known = {
        ADAPTER_PROVIDERS[adapter]
        for adapter in adapters
        if adapter in ADAPTER_PROVIDERS
    }
    if len(known) > 1:
        raise ValueError("PROVIDER_CONFLICT: " + ",".join(sorted(known)))
    if known:
        return next(iter(known))
    return None


def load_session_rows(conn: Any) -> list[dict[str, Any]]:
    """Read provider-safe allocation metadata from a Session Search projection.

    The returned rows contain private session IDs for in-process deterministic
    allocation only. Call public_summary() before serializing/public reporting.
    """

    adapters_by_session: dict[str, set[str]] = defaultdict(set)
    for session_id, adapter in conn.execute(
        """
        SELECT r.projected_session_id, a.source_adapter
        FROM artifact_routes AS r
        JOIN artifacts AS a ON a.artifact_id=r.artifact_id
        ORDER BY r.projected_session_id, a.source_adapter
        """
    ):
        adapters_by_session[str(session_id)].add(str(adapter))

    rows: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT
            s.session_id,
            s.first_accepted_at,
            COALESCE(SUM(
                CASE
                    WHEN m.search_class IN ('dialogue','evidence','trace')
                    THEN 1 ELSE 0
                END
            ),0) AS searchable_messages,
            COALESCE(SUM(
                CASE
                    WHEN m.search_class IN ('dialogue','evidence','trace')
                    THEN length(CAST(m.text AS BLOB)) ELSE 0
                END
            ),0) AS searchable_text_bytes
        FROM sessions AS s
        LEFT JOIN messages AS m ON m.session_id=s.session_id
        GROUP BY s.session_id, s.first_accepted_at
        ORDER BY s.session_id
        """
    ):
        session_id = str(row[0])
        adapters = adapters_by_session.get(session_id, set())
        if not adapters:
            raise ValueError("SESSION_WITHOUT_ROUTED_PROVENANCE")
        rows.append(
            {
                "session_id": session_id,
                "first_accepted_at": row[1],
                "provider": provider_from_adapters(adapters),
                "searchable_messages": int(row[2]),
                "searchable_text_bytes": int(row[3]),
                "adapters": tuple(sorted(adapters)),
            }
        )
    return rows


def _finalize_shard(
    provider: str,
    number: int,
    rows: Sequence[Mapping[str, Any]],
    estimated_bytes: int,
    target_bytes: int,
    hard_limit_bytes: int,
) -> dict[str, Any]:
    return {
        "shard_id": f"{provider}-{number:04d}",
        "provider": provider,
        "session_ids": [str(row["session_id"]) for row in rows],
        "sessions": len(rows),
        "searchable_messages": sum(int(row["searchable_messages"]) for row in rows),
        "searchable_text_bytes": sum(
            int(row["searchable_text_bytes"]) for row in rows
        ),
        "estimated_postgres_bytes": estimated_bytes,
        "over_target": estimated_bytes > target_bytes,
        "hard_limit_bytes": hard_limit_bytes,
    }


def allocate_sessions(
    rows: Sequence[Mapping[str, Any]],
    *,
    target_bytes: int = DEFAULT_TARGET_BYTES,
    hard_limit_bytes: int = HARD_LIMIT_BYTES,
) -> dict[str, Any]:
    if target_bytes <= FIXED_OVERHEAD_BYTES:
        raise ValueError("TARGET_TOO_SMALL")
    if hard_limit_bytes <= target_bytes:
        raise ValueError("HARD_LIMIT_MUST_EXCEED_TARGET")

    by_provider: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    unresolved: list[Mapping[str, Any]] = []

    for row in rows:
        provider = row.get("provider")
        if provider is None:
            unresolved.append(row)
            continue
        if not isinstance(provider, str) or not provider:
            raise ValueError("PROVIDER_INVALID")
        if not row.get("first_accepted_at"):
            raise ValueError("FIRST_ACCEPTED_AT_MISSING")
        by_provider[provider].append(row)

    shards: list[dict[str, Any]] = []
    for provider in sorted(by_provider):
        ordered = sorted(
            by_provider[provider],
            key=lambda row: (str(row["first_accepted_at"]), str(row["session_id"])),
        )
        current: list[Mapping[str, Any]] = []
        current_bytes = FIXED_OVERHEAD_BYTES
        shard_number = 1

        for row in ordered:
            session_bytes = estimate_session_bytes(
                int(row["searchable_messages"]), int(row["searchable_text_bytes"])
            )
            single_total = FIXED_OVERHEAD_BYTES + session_bytes
            if single_total > hard_limit_bytes:
                raise ValueError("SESSION_EXCEEDS_HARD_LIMIT")

            if current and current_bytes + session_bytes > target_bytes:
                shards.append(
                    _finalize_shard(
                        provider,
                        shard_number,
                        current,
                        current_bytes,
                        target_bytes,
                        hard_limit_bytes,
                    )
                )
                shard_number += 1
                current = []
                current_bytes = FIXED_OVERHEAD_BYTES

            current.append(row)
            current_bytes += session_bytes

        if current:
            shards.append(
                _finalize_shard(
                    provider,
                    shard_number,
                    current,
                    current_bytes,
                    target_bytes,
                    hard_limit_bytes,
                )
            )

    return {
        "target_bytes": target_bytes,
        "hard_limit_bytes": hard_limit_bytes,
        "shards": shards,
        "unresolved": sorted(
            unresolved,
            key=lambda row: (
                str(row.get("first_accepted_at") or ""), str(row["session_id"])
            ),
        ),
    }


def _headroom_tenths(estimated: int, hard_limit: int) -> int:
    # Percentage * 10, rounded to nearest tenth without float arithmetic.
    remaining = hard_limit - estimated
    return max(0, (remaining * 1000 + hard_limit // 2) // hard_limit)


def public_summary(allocation: Mapping[str, Any]) -> dict[str, Any]:
    hard_limit = int(allocation["hard_limit_bytes"])
    shards = []
    for shard in allocation["shards"]:
        estimated = int(shard["estimated_postgres_bytes"])
        tenths = _headroom_tenths(estimated, hard_limit)
        shards.append(
            {
                "shard_id": shard["shard_id"],
                "provider": shard["provider"],
                "sessions": int(shard["sessions"]),
                "searchable_messages": int(shard["searchable_messages"]),
                "searchable_text_bytes": int(shard["searchable_text_bytes"]),
                "estimated_postgres_bytes": estimated,
                "estimated_mib": round(estimated / (1024 * 1024), 1),
                "hard_limit_headroom_percent": tenths / 10,
                "over_target": bool(shard["over_target"]),
            }
        )

    unresolved_rows = allocation["unresolved"]
    unresolved_adapters = sorted(
        {
            adapter
            for row in unresolved_rows
            for adapter in row.get("adapters", ())
        }
    )

    return {
        "schema": "theseus.session-search-shard-allocation-public.v1",
        "model": {
            "contract_version": ALLOCATION_CONTRACT_VERSION,
            "atomic_unit": "projected_session",
            "target_bytes": int(allocation["target_bytes"]),
            "hard_limit_bytes": hard_limit,
            "hard_limit_semantics": "decimal_1GB_project_limit",
            "fixed_overhead_bytes": FIXED_OVERHEAD_BYTES,
            "per_message_ratio": "97897/10",
            "per_text_byte_ratio": "55839/10000",
            "ordering": "provider,first_accepted_at,private_session_id",
        },
        "shards": shards,
        "unresolved_provider": {
            "sessions": len(unresolved_rows),
            "searchable_messages": sum(
                int(row["searchable_messages"]) for row in unresolved_rows
            ),
            "searchable_text_bytes": sum(
                int(row["searchable_text_bytes"]) for row in unresolved_rows
            ),
            "adapters": unresolved_adapters,
        },
        "totals": {
            "assigned_sessions": sum(item["sessions"] for item in shards),
            "assigned_searchable_messages": sum(
                item["searchable_messages"] for item in shards
            ),
            "assigned_searchable_text_bytes": sum(
                item["searchable_text_bytes"] for item in shards
            ),
            "unresolved_sessions": len(unresolved_rows),
        },
    }
