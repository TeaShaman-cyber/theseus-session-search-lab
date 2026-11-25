"""Bounded reference snapshots of projected evidence; no source-currentness claim."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .accepted_membership import (
    CURRENTNESS, _capture_locked, _digest, _hash, _scope, _validated_members,
    compare_accepted_membership,
)
from .corpus_store import CorpusMutationLock, CorpusPaths

SCHEMA = "theseus.session-search-session-evidence.v1"
CLASSES = ("dialogue", "evidence", "trace")


def _limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 100_000:
        raise ValueError("INVALID_EVIDENCE_LIMIT")
    return value


def capture_session_evidence(
    corpus_root: Path, *, scope: str, session_id: str,
    max_events: int, max_sources: int,
) -> dict:
    """Capture one session under the same lock as accepted membership.

    Event identity is projected session/local identity, not equal text.
    The receipt is metadata-only but contains sensitive identifiers.
    The projection consistency check is not full artifact derivation.
    """
    scope, session_id = _scope(scope), _scope(session_id)
    max_events, max_sources = _limit(max_events), _limit(max_sources)
    paths = CorpusPaths.from_root(corpus_root)
    if not paths.accepted_ledger.is_dir() or not paths.db.is_file():
        raise ValueError("CORPUS_NOT_INITIALIZED")
    with CorpusMutationLock(paths, "session-evidence-snapshot"):
        membership = _capture_locked(paths, scope)
        con = sqlite3.connect(paths.db.resolve().as_uri() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            session = con.execute(
                "SELECT coverage_state,coverage_reason FROM sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise ValueError("SESSION_NOT_FOUND")
            rows = con.execute(
                """
                SELECT row_id,local_identity,message_id,canonical_message_sha256,
                       role,content_type,search_class,create_time,provider_order,text
                FROM messages WHERE session_id=? AND text<>'' AND search_class IN (?,?,?)
                ORDER BY local_identity LIMIT ?
                """, (session_id, *CLASSES, max_events + 1),
            ).fetchall()
            if len(rows) > max_events:
                raise ValueError("EVENT_LIMIT_EXCEEDED")
            sources = con.execute(
                """
                SELECT m.local_identity,a.sha256,p.capture_sequence,ms.page_position,
                       ms.source_message_id,ms.source_object_sha256,p.member_name
                FROM messages m JOIN message_sources ms ON ms.message_row_id=m.row_id
                JOIN payload_pages p ON p.page_id=ms.page_id
                JOIN artifacts a ON a.artifact_id=p.artifact_id
                WHERE m.session_id=? AND m.text<>'' AND m.search_class IN (?,?,?)
                ORDER BY m.local_identity,a.sha256,p.capture_sequence,ms.page_position
                LIMIT ?
                """, (session_id, *CLASSES, max_sources + 1),
            ).fetchall()
            if len(sources) > max_sources:
                raise ValueError("SOURCE_LIMIT_EXCEEDED")
            known = _validated_members(membership)
            by_event = {row["local_identity"]: [] for row in rows}
            for source in sources:
                item = dict(source)
                identity = item.pop("local_identity")
                if item["sha256"] not in known or identity not in by_event:
                    raise ValueError("EVIDENCE_SOURCE_MISMATCH")
                by_event[identity].append(item)
            events = []
            for row in rows:
                item = dict(row)
                item.pop("row_id")
                identity = item.pop("local_identity")
                _hash(item["canonical_message_sha256"])
                if not by_event[identity]:
                    raise ValueError("EVIDENCE_WITHOUT_SOURCE")
                events.append({
                    "local_identity": identity,
                    "evidence_sha256": _digest(item),
                    "sources": by_event[identity],
                })
            core = {
                "schema": SCHEMA,
                "accepted_membership": membership,
                "session_id": session_id,
                "currentness": CURRENTNESS,
                "coverage": dict(session),
                "events": events,
            }
            result = {**core, "snapshot_sha256": _digest(core)}
            _events(result)
            return result
        finally:
            con.close()


def _events(snapshot: Any) -> dict:
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "schema", "accepted_membership", "session_id", "currentness",
        "coverage", "events", "snapshot_sha256",
    } or snapshot["schema"] != SCHEMA or snapshot["currentness"] != CURRENTNESS:
        raise ValueError("INVALID_EVIDENCE_SNAPSHOT")
    _scope(snapshot["session_id"])
    known = _validated_members(snapshot["accepted_membership"])
    coverage = snapshot["coverage"]
    if not isinstance(coverage, dict) or set(coverage) != {"coverage_state", "coverage_reason"}:
        raise ValueError("INVALID_EVIDENCE_COVERAGE")
    if not all(isinstance(value, str) for value in coverage.values()):
        raise ValueError("INVALID_EVIDENCE_COVERAGE")
    if not isinstance(snapshot["events"], list):
        raise ValueError("INVALID_EVIDENCE_EVENTS")
    result = {}
    for event in snapshot["events"]:
        if not isinstance(event, dict) or set(event) != {"local_identity", "evidence_sha256", "sources"}:
            raise ValueError("INVALID_EVIDENCE_EVENT")
        identity = _scope(event["local_identity"])
        _hash(event["evidence_sha256"])
        if identity in result:
            raise ValueError("DUPLICATE_EVIDENCE_EVENT")
        sources = event["sources"]
        if not isinstance(sources, list) or not sources:
            raise ValueError("EVIDENCE_WITHOUT_SOURCE")
        keys = []
        for source in sources:
            if not isinstance(source, dict) or set(source) != {
                "sha256", "capture_sequence", "page_position", "source_message_id",
                "source_object_sha256", "member_name",
            }:
                raise ValueError("INVALID_EVIDENCE_SOURCE")
            if _hash(source["sha256"]) not in known:
                raise ValueError("EVIDENCE_SOURCE_NOT_ACCEPTED")
            _hash(source["source_object_sha256"])
            _scope(source["member_name"])
            if source["source_message_id"] is not None and not isinstance(source["source_message_id"], str):
                raise ValueError("INVALID_EVIDENCE_SOURCE")
            positions = (source["capture_sequence"], source["page_position"])
            if any(type(value) is not int or value < 0 for value in positions):
                raise ValueError("INVALID_EVIDENCE_SOURCE")
            keys.append((source["sha256"], *positions))
        if keys != sorted(set(keys)):
            raise ValueError("NONCANONICAL_EVIDENCE_SOURCES")
        result[identity] = event
    if list(result) != sorted(result):
        raise ValueError("NONCANONICAL_EVIDENCE_ORDER")
    core = {k: v for k, v in snapshot.items() if k != "snapshot_sha256"}
    if snapshot["snapshot_sha256"] != _digest(core):
        raise ValueError("EVIDENCE_SNAPSHOT_DIGEST_MISMATCH")
    return result


def compare_session_evidence(previous: dict, current: dict) -> dict:
    """Reference diff; removed events require reconciliation, not automatic deletion."""
    before, after = _events(previous), _events(current)
    if previous["session_id"] != current["session_id"]:
        raise ValueError("SESSION_SCOPE_MISMATCH")
    compare_accepted_membership(previous["accepted_membership"], current["accepted_membership"])
    common = before.keys() & after.keys()
    changed = sorted(key for key in common if before[key]["evidence_sha256"] != after[key]["evidence_sha256"])
    provenance_only = sorted(key for key in common if key not in changed and before[key]["sources"] != after[key]["sources"])
    return {
        "schema": "theseus.session-search-session-evidence-delta.v1",
        "session_id": current["session_id"],
        "currentness": CURRENTNESS,
        "previous_snapshot_sha256": previous["snapshot_sha256"],
        "current_snapshot_sha256": current["snapshot_sha256"],
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "changed": changed,
        "provenance_only": provenance_only,
        "coverage_changed": previous["coverage"] != current["coverage"],
    }
