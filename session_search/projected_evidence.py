"""Common projected evidence contract for SQLite and generation-pinned Neon.

Only fields actually carried by both projections participate. The accepted
corpus receipt is a separate, stronger source-provenance witness.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

from .accepted_membership import CURRENTNESS, _capture_locked, _digest, _hash, _scope
from .corpus_store import CorpusMutationLock, CorpusPaths
from .shard_allocation import ADAPTER_PROVIDERS
from .session_evidence_delta import CLASSES, _limit

SCHEMA = "theseus.session-search-projected-evidence.v1"


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("INVALID_PROJECTION_INTEGER")
    return value


def _time(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("INVALID_PROJECTION_TIME")
    return float(value) or 0.0


def _metadata(row):
    mid = _scope(row["message_id"])
    role = _scope(row["role"])
    kind = row["search_class"]
    if kind not in CLASSES:
        raise ValueError("INVALID_PROJECTION_CLASS")
    return {
        "message_id": mid, "ordinal": _integer(row["ordinal"]),
        "role": role, "search_class": kind, "create_time": _time(row["create_time"]),
    }


def _event(metadata, body):
    if not isinstance(body, str) or "\x00" in body:
        raise ValueError("UNREPRESENTABLE_PROJECTION_TEXT")
    # Ordinal is a context dependency, not the identity/version of a standalone event.
    core = {k: v for k, v in metadata.items() if k != "ordinal"}
    return {"message_id": metadata["message_id"], "ordinal": metadata["ordinal"],
            "evidence_sha256": _digest({**core, "text": body})}


def _receipt(scope, provider, session_id, coverage, events, lineage):
    _scope(scope)
    _scope(provider)
    _scope(session_id)
    _scope(coverage)
    core = {"schema": SCHEMA, "scope": scope, "provider": provider, "session_id": session_id,
            "coverage": coverage, "currentness": CURRENTNESS,
            "events": sorted(events, key=lambda e: e["message_id"]), "lineage": lineage}
    result = {**core, "snapshot_sha256": _digest(core)}
    _validate(result)
    return result


def _validate(receipt):
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema", "scope", "provider", "session_id", "coverage", "currentness", "events",
        "lineage", "snapshot_sha256",
    } or receipt["schema"] != SCHEMA or receipt["currentness"] != CURRENTNESS:
        raise ValueError("INVALID_PROJECTION_RECEIPT")
    for key in ("scope", "provider", "session_id", "coverage"):
        _scope(receipt[key])
    lineage = receipt["lineage"]
    if not isinstance(lineage, dict):
        raise ValueError("INVALID_PROJECTION_LINEAGE")
    if lineage.get("backend") == "sqlite" and set(lineage) == {"backend", "accepted_snapshot_sha256"}:
        _hash(lineage["accepted_snapshot_sha256"])
    elif lineage.get("backend") == "neon" and set(lineage) == {"backend", "shard_id", "generation_id"}:
        _scope(lineage["shard_id"])
        _hash(lineage["generation_id"])
    else:
        raise ValueError("INVALID_PROJECTION_LINEAGE")
    if not isinstance(receipt["events"], list):
        raise ValueError("INVALID_PROJECTION_EVENTS")
    result = {}
    for event in receipt["events"]:
        if not isinstance(event, dict) or set(event) != {"message_id", "ordinal", "evidence_sha256"}:
            raise ValueError("INVALID_PROJECTION_EVENT")
        mid = _scope(event["message_id"])
        if mid in result:
            raise ValueError("DUPLICATE_PROJECTED_EVENT")
        _integer(event["ordinal"])
        _hash(event["evidence_sha256"])
        result[mid] = event
    if list(result) != sorted(result):
        raise ValueError("NONCANONICAL_PROJECTION_EVENTS")
    if receipt["snapshot_sha256"] != _digest({k: v for k, v in receipt.items() if k != "snapshot_sha256"}):
        raise ValueError("PROJECTION_RECEIPT_DIGEST_MISMATCH")
    return result


def capture_local_projection(corpus_root: Path, *, scope: str, provider: str, session_id: str,
                             max_events: int, max_bytes: int):
    """Read only the Neon-materializable fields under the existing corpus lock."""
    scope, provider, session_id = map(_scope, (scope, provider, session_id))
    max_events, max_bytes = _limit(max_events), _limit(max_bytes)
    paths = CorpusPaths.from_root(corpus_root)
    if not paths.accepted_ledger.is_dir() or not paths.db.is_file():
        raise ValueError("CORPUS_NOT_INITIALIZED")
    with CorpusMutationLock(paths, "projected-evidence-snapshot"):
        accepted = _capture_locked(paths, scope)
        con = sqlite3.connect(paths.db.resolve().as_uri() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            session = con.execute("SELECT coverage_state FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if session is None:
                raise ValueError("SESSION_NOT_FOUND")
            adapters = [r[0] for r in con.execute(
                "SELECT DISTINCT a.source_adapter FROM artifact_routes r JOIN artifacts a "
                "ON a.artifact_id=r.artifact_id WHERE r.projected_session_id=?", (session_id,))]
            if not adapters or {ADAPTER_PROVIDERS.get(a) for a in adapters} != {provider}:
                raise ValueError("PROJECTION_PROVIDER_MISMATCH")
            args = (session_id, *CLASSES)
            counts = con.execute(
                "SELECT count(*),coalesce(sum(length(CAST(text AS BLOB))),0) FROM messages "
                "WHERE session_id=? AND search_class IN (?,?,?)", args).fetchone()
            # Conservative UTF-8 byte budget on local path; same total byte bound below.
            if counts[0] > max_events or counts[1] > max_bytes:
                raise ValueError("PROJECTION_BUDGET_EXCEEDED")
            rows = con.execute(
                "SELECT ordinal,coalesce(message_id,local_identity) AS message_id,role,search_class,create_time,text "
                "FROM messages WHERE session_id=? AND search_class IN (?,?,?) ORDER BY ordinal,local_identity", args)
            events = [_event(_metadata(r), r["text"]) for r in rows]
            return _receipt(scope, provider, session_id, session["coverage_state"], events,
                            {"backend": "sqlite", "accepted_snapshot_sha256": accepted["snapshot_sha256"]})
        finally:
            con.close()


def capture_neon_projection(fetch_page, *, scope: str, provider: str, shard_id: str, generation_id: str,
                            session_id: str, max_events: int, max_bytes: int, max_pages: int):
    """Consume bounded pages from the existing pinned session_browse contract.

    fetch_page(position, limit, max_chars) is supplied by the trusted read route.
    No ACTIVE fallback or search-hit surrogate. Any incomplete read raises.
    max_bytes is a total UTF-8 byte budget (SQL page limits are characters).
    """
    scope, provider, shard_id, session_id = map(_scope, (scope, provider, shard_id, session_id))
    _hash(generation_id)
    max_events, max_bytes, max_pages = map(_limit, (max_events, max_bytes, max_pages))
    position = {"ordinal": 0, "message_id": None, "offset": 0}
    events, seen = [], set()
    current, parts, total, offset, used = None, [], None, 0, 0
    coverage = None
    previous_key = None
    for _ in range(max_pages):
        page = fetch_page(dict(position), min(100, max_events), min(100000, max_bytes))
        if not isinstance(page, dict) or any(page.get(k) != v for k, v in {
            "status": "OK", "provider": provider, "shard_id": shard_id,
            "generation_binding": "router_pinned_v1", "unavailable_shards": [],
            "queried_shards": [shard_id],
            "generation_id": generation_id, "session_id": session_id,
        }.items()):
            raise ValueError("PROJECTION_PAGE_BINDING_MISMATCH")
        new_coverage = _scope(page.get("session_coverage"))
        if coverage is not None and coverage != new_coverage:
            raise ValueError("PROJECTION_COVERAGE_DRIFT")
        coverage = new_coverage
        segments = page.get("messages")
        if not isinstance(segments, list) or len(segments) > min(100, max_events):
            raise ValueError("INVALID_PROJECTION_PAGE")
        if not segments and position["message_id"] is not None:
            raise ValueError("PROJECTION_PREMATURE_END")
        if type(page.get("has_more")) is not bool:
            raise ValueError("INVALID_PROJECTION_PAGE")
        page_chars = 0
        for index, segment in enumerate(segments):
            meta = _metadata(segment)
            start = _integer(segment["text_offset"])
            length = _integer(segment["text_total_chars"])
            text = segment["text"]
            complete = segment["complete_message"]
            if not isinstance(text, str) or type(complete) is not bool or "\x00" in text:
                raise ValueError("INVALID_PROJECTION_SEGMENT")
            if current is None:
                key = (meta["ordinal"], meta["message_id"].encode("utf-8"))
                if start != 0 or meta["message_id"] in seen or (previous_key is not None and key <= previous_key):
                    raise ValueError("PROJECTION_EVENT_ORDER")
                current, total, offset, parts = meta, length, 0, []
                previous_key = key
            if meta != current or length != total or start != offset:
                raise ValueError("PROJECTION_SEGMENT_MISMATCH")
            page_chars += len(text)
            if page_chars > min(100000, max_bytes) or (not complete and index != len(segments)-1):
                raise ValueError("INVALID_PROJECTION_PAGE")
            offset += len(text)
            used += len(text.encode("utf-8"))
            if used > max_bytes or len(events) >= max_events:
                raise ValueError("PROJECTION_BUDGET_EXCEEDED")
            if offset > total or complete != (offset == total) or (not complete and not text):
                raise ValueError("PROJECTION_SEGMENT_LENGTH")
            parts.append(text)
            position = {"ordinal": meta["ordinal"], "message_id": meta["message_id"],
                        "offset": 0 if complete else offset}
            if complete:
                events.append(_event(meta, "".join(parts)))
                seen.add(meta["message_id"])
                current, parts = None, []
        if not page["has_more"]:
            if current is not None or page.get("next_position") is not None:
                raise ValueError("PROJECTION_INCOMPLETE")
            return _receipt(scope, provider, session_id, coverage, events,
                            {"backend": "neon", "shard_id": shard_id, "generation_id": generation_id})
        if not segments or page.get("next_position") != position:
            raise ValueError("PROJECTION_CURSOR_MISMATCH")
    raise ValueError("PROJECTION_PAGE_LIMIT_EXCEEDED")


def compare_projected_evidence(previous, current):
    """Common comparison; no inference of full source identity or provenance."""
    before, after = _validate(previous), _validate(current)
    if any(previous[k] != current[k] for k in ("scope", "provider", "session_id")):
        raise ValueError("PROJECTION_SCOPE_MISMATCH")
    common = before.keys() & after.keys()
    return {
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "changed": sorted(k for k in common if before[k]["evidence_sha256"] != after[k]["evidence_sha256"]),
        "order_changed": sorted(k for k in common if before[k]["ordinal"] != after[k]["ordinal"]),
        "coverage_changed": previous["coverage"] != current["coverage"],
        "currentness": CURRENTNESS,
        "previous_snapshot_sha256": previous["snapshot_sha256"],
        "current_snapshot_sha256": current["snapshot_sha256"],
    }
