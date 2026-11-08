from __future__ import annotations

from typing import Any


def _identity(row: dict[str, Any]) -> tuple[str, int, str]:
    if not isinstance(row, dict):
        raise ValueError("RRF_ROW_INVALID")
    session_id = row.get("session_id")
    ordinal = row.get("ordinal")
    text_fp = row.get("text_fp")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("RRF_ROW_INVALID")
    if not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("RRF_ROW_INVALID")
    if not isinstance(text_fp, str) or not text_fp:
        raise ValueError("RRF_ROW_INVALID")
    return session_id, ordinal, text_fp


def _index(rows: list[dict[str, Any]], *, label: str) -> dict[tuple[str, int, str], tuple[int, dict[str, Any]]]:
    if not isinstance(rows, list):
        raise ValueError("RRF_ROWS_INVALID")
    out: dict[tuple[str, int, str], tuple[int, dict[str, Any]]] = {}
    for rank, row in enumerate(rows, 1):
        ident = _identity(row)
        if ident in out:
            raise ValueError(f"RRF_DUPLICATE_CANDIDATE:{label}")
        out[ident] = (rank, row)
    return out


def fuse_rrf(
    full_rows: list[dict[str, Any]],
    sparse_rows: list[dict[str, Any]],
    *,
    k: int = 60,
    limit: int = 30,
) -> list[dict[str, Any]]:
    if not isinstance(k, int) or k < 1:
        raise ValueError("RRF_K_INVALID")
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("RRF_LIMIT_INVALID")

    full = _index(full_rows, label="full")
    sparse = _index(sparse_rows, label="sparse")

    fused: list[dict[str, Any]] = []
    for ident in sorted(set(full) | set(sparse)):
        full_item = full.get(ident)
        sparse_item = sparse.get(ident)
        full_rank = full_item[0] if full_item else None
        sparse_rank = sparse_item[0] if sparse_item else None
        source = full_item[1] if full_item else sparse_item[1]

        score = 0.0
        if full_rank is not None:
            score += 1.0 / (k + full_rank)
        if sparse_rank is not None:
            score += 1.0 / (k + sparse_rank)

        best_rank = min(
            rank for rank in (full_rank, sparse_rank) if rank is not None
        )
        row = dict(source)
        row.update(
            {
                "rrf_score": score,
                "best_view_rank": best_rank,
                "full_rank": full_rank,
                "sparse_rank": sparse_rank,
            }
        )
        fused.append(row)

    inf = 10**12
    fused.sort(
        key=lambda row: (
            -row["rrf_score"],
            row["best_view_rank"],
            row["full_rank"] if row["full_rank"] is not None else inf,
            row["sparse_rank"] if row["sparse_rank"] is not None else inf,
            row["session_id"],
            row["ordinal"],
        )
    )
    return fused[:limit]
