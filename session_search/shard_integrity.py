from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .artifact_overlap import set_digest
from .corpus_store import (
    CorpusMutationLock,
    CorpusPaths,
    _populate_projection_from_entries,
    read_accepted_ledger,
)
from .neon_materializer import (
    _load_rows_for_sessions,
    _manifest_from_snapshot,
    _select_shard_assignment,
    _source_connection,
)

SCHEMA = "theseus.session-search-shard-source-integrity.v1"
MODE = "SHARD_BOUNDED_REPLAY_V2"


def _result(status: str, provider: str, shard_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": status,
        "verification_mode": MODE,
        "provider": provider,
        "shard_id": shard_id,
        **extra,
    }


def _route_rows(con: sqlite3.Connection, session_ids: tuple[str, ...]) -> list[tuple[str, ...]]:
    marks = ",".join("?" for _ in session_ids)
    return [
        tuple(str(value) for value in row)
        for row in con.execute(
            "SELECT a.sha256,r.accepted_session_id,r.projected_session_id,"
            "r.route_state,r.route_reason,r.route_version,a.source_adapter "
            "FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id "
            f"WHERE r.projected_session_id IN ({marks}) ORDER BY a.sha256",
            session_ids,
        )
    ]


def _all_route_rows(con: sqlite3.Connection) -> list[tuple[str, ...]]:
    return [
        tuple(str(value) for value in row)
        for row in con.execute(
            "SELECT a.sha256,r.accepted_session_id,r.projected_session_id,"
            "r.route_state,r.route_reason,r.route_version,a.source_adapter "
            "FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id "
            "ORDER BY a.sha256"
        )
    ]


def _route_rows_for_artifacts(
    con: sqlite3.Connection, artifact_shas: set[str] | tuple[str, ...]
) -> list[tuple[str, ...]]:
    wanted = set(artifact_shas)
    return [row for row in _all_route_rows(con) if row[0] in wanted]


def _chatgpt_family_closure_shas(
    all_routes: list[tuple[str, ...]], target_routes: list[tuple[str, ...]]
) -> tuple[set[str], tuple[str, ...]]:
    family_ids = tuple(sorted({row[1] for row in target_routes if row[3] != "DIRECT"}))
    closure_shas = {row[0] for row in target_routes}
    if not family_ids:
        return closure_shas, family_ids
    for row in all_routes:
        accepted_session_id = row[1]
        source_adapter = row[6]
        if accepted_session_id in family_ids:
            closure_shas.add(row[0])
            continue
        if source_adapter == "chatgpt-export" and any(
            accepted_session_id.startswith(f"{family_id}~branch-")
            for family_id in family_ids
        ):
            closure_shas.add(row[0])
    return closure_shas, family_ids


def verify_shard_source_integrity(
    corpus_root: Path, *, provider: str, shard_id: str
) -> dict[str, Any]:
    corpus_root = Path(corpus_root)
    paths = CorpusPaths.from_root(corpus_root)
    with CorpusMutationLock(paths, "verify-shard-source"):
        accepted = read_accepted_ledger(paths)
        accepted_shas = set(accepted)
        con = _source_connection(corpus_root)
        try:
            database_shas = {
                str(row[0])
                for row in con.execute("SELECT sha256 FROM artifacts ORDER BY sha256")
            }
            if database_shas != accepted_shas:
                return _result(
                    "RECONCILIATION_REQUIRED",
                    provider,
                    shard_id,
                    reason="accepted ledger/projection membership disagreement",
                    accepted_artifact_count=len(accepted_shas),
                    projection_artifact_count=len(database_shas),
                )

            route_count = int(con.execute("SELECT count(*) FROM artifact_routes").fetchone()[0])
            missing_route_count = int(con.execute(
                "SELECT count(*) FROM artifacts a LEFT JOIN artifact_routes r "
                "ON r.artifact_id=a.artifact_id WHERE r.artifact_id IS NULL"
            ).fetchone()[0])
            orphan_route_count = int(con.execute(
                "SELECT count(*) FROM artifact_routes r LEFT JOIN artifacts a "
                "ON a.artifact_id=r.artifact_id WHERE a.artifact_id IS NULL"
            ).fetchone()[0])
            if (
                route_count != len(database_shas)
                or missing_route_count != 0
                or orphan_route_count != 0
            ):
                return _result(
                    "RECONCILIATION_REQUIRED",
                    provider,
                    shard_id,
                    reason="artifact route membership disagreement",
                    projection_artifact_count=len(database_shas),
                    route_count=route_count,
                    missing_route_count=missing_route_count,
                    orphan_route_count=orphan_route_count,
                )

            shard, session_ids, adapters = _select_shard_assignment(
                con, provider=provider, shard_id=shard_id
            )
            current_routes = _route_rows(con, session_ids)
            if not current_routes:
                return _result(
                    "RECONCILIATION_REQUIRED",
                    provider,
                    shard_id,
                    reason="shard has no routed artifacts",
                )
            route_states = sorted({row[3] for row in current_routes})
            unsupported_states = sorted(
                state for state in route_states if state not in {"DIRECT", "BRANCH_MATCH", "UNRESOLVED"}
            )
            if unsupported_states:
                return _result(
                    "UNSUPPORTED_ROUTE_CLOSURE",
                    provider,
                    shard_id,
                    route_states=route_states,
                    reason="shard contains unsupported route state",
                )
            if route_states != ["DIRECT"] and provider != "chatgpt":
                return _result(
                    "UNSUPPORTED_ROUTE_CLOSURE",
                    provider,
                    shard_id,
                    route_states=route_states,
                    reason="non-DIRECT family closure is only defined for ChatGPT",
                )
            target_artifact_shas = tuple(row[0] for row in current_routes)
            if len(set(target_artifact_shas)) != len(target_artifact_shas):
                return _result(
                    "RECONCILIATION_REQUIRED",
                    provider,
                    shard_id,
                    reason="duplicate routed artifact identity",
                )
            all_routes = _all_route_rows(con)
            closure_artifact_shas, family_ids = _chatgpt_family_closure_shas(
                all_routes, current_routes
            )
            missing = [sha for sha in sorted(closure_artifact_shas) if sha not in accepted]
            if missing:
                return _result(
                    "RECONCILIATION_REQUIRED",
                    provider,
                    shard_id,
                    reason="routed artifact missing from accepted ledger",
                    missing_artifacts=len(missing),
                )
            selected_entries = {sha: accepted[sha] for sha in sorted(closure_artifact_shas)}
            current_closure_routes = [row for row in all_routes if row[0] in closure_artifact_shas]
            current_sessions, current_messages = _load_rows_for_sessions(con, session_ids)
        finally:
            con.close()

        current_manifest = _manifest_from_snapshot(
            accepted_shas,
            current_sessions,
            current_messages,
            adapters,
            provider=provider,
            shard_id=shard_id,
        )
        if len(current_sessions) != int(shard["sessions"]):
            return _result("RECONCILIATION_REQUIRED", provider, shard_id, reason="allocator/session mismatch")
        if len(current_messages) != int(shard["searchable_messages"]):
            return _result("RECONCILIATION_REQUIRED", provider, shard_id, reason="allocator/message mismatch")
        if sum(len(row[6].encode("utf-8")) for row in current_messages) != int(shard["searchable_text_bytes"]):
            return _result("RECONCILIATION_REQUIRED", provider, shard_id, reason="allocator/text-byte mismatch")

        with tempfile.TemporaryDirectory(prefix="session-search-shard-verify-") as td:
            candidate = Path(td) / "derived.sqlite3"
            try:
                _populate_projection_from_entries(paths, candidate, selected_entries)
            except RuntimeError:
                return _result(
                    "FAILED_INTEGRITY",
                    provider,
                    shard_id,
                    reason="selected accepted artifact replay failed",
                    shard_artifact_count=len(selected_entries),
                )

            replay = sqlite3.connect(candidate)
            try:
                integrity = str(replay.execute("PRAGMA integrity_check").fetchone()[0])
                if integrity != "ok":
                    return _result(
                        "FAILED_INTEGRITY",
                        provider,
                        shard_id,
                        reason="replayed shard sqlite integrity failure",
                        sqlite_integrity=integrity,
                    )
                foreign_keys = replay.execute("PRAGMA foreign_key_check").fetchall()
                if foreign_keys:
                    return _result(
                        "FAILED_INTEGRITY",
                        provider,
                        shard_id,
                        reason="replayed shard foreign key failure",
                        foreign_key_rows=len(foreign_keys),
                    )
                replay_session_ids = tuple(
                    str(row[0])
                    for row in replay.execute("SELECT session_id FROM sessions ORDER BY session_id")
                )
                if not set(session_ids).issubset(set(replay_session_ids)):
                    return _result(
                        "RECONCILIATION_REQUIRED",
                        provider,
                        shard_id,
                        reason="replayed session membership mismatch",
                    )
                replay_sessions, replay_messages = _load_rows_for_sessions(replay, session_ids)
                replay_routes = _route_rows_for_artifacts(replay, set(selected_entries))
            finally:
                replay.close()

        if replay_routes != current_closure_routes:
            return _result(
                "RECONCILIATION_REQUIRED",
                provider,
                shard_id,
                reason="routed artifact replay mismatch",
            )
        if replay_sessions != current_sessions:
            return _result(
                "RECONCILIATION_REQUIRED",
                provider,
                shard_id,
                reason="session projection replay mismatch",
            )
        if replay_messages != current_messages:
            return _result(
                "RECONCILIATION_REQUIRED",
                provider,
                shard_id,
                reason="searchable message projection replay mismatch",
            )

        replay_manifest = _manifest_from_snapshot(
            accepted_shas,
            replay_sessions,
            replay_messages,
            adapters,
            provider=provider,
            shard_id=shard_id,
        )
        if replay_manifest != current_manifest:
            return _result(
                "RECONCILIATION_REQUIRED",
                provider,
                shard_id,
                reason="source manifest replay mismatch",
            )

        return _result(
            "VERIFIED",
            provider,
            shard_id,
            accepted_artifact_count=len(accepted_shas),
            accepted_set_sha256=set_digest(accepted_shas),
            shard_artifact_count=len(set(target_artifact_shas)),
            closure_artifact_count=len(selected_entries),
            route_states=route_states,
            family_closure_count=len(family_ids),
            session_count=len(current_sessions),
            message_count=len(current_messages),
            searchable_text_bytes=current_manifest["searchable_text_bytes"],
            source_projection_sha256=current_manifest["source_projection_sha256"],
            source_manifest_sha256=current_manifest["source_manifest_sha256"],
            generation_id=current_manifest["generation_id"],
            sqlite_integrity="ok",
            foreign_key_rows=0,
            verified_components=[
                "global_accepted_projection_membership",
                "global_artifact_route_membership",
                "selected_accepted_blob_hash_and_metadata",
                "provider_aware_allocator_membership",
                "bounded_route_family_replay",
                "session_projection_replay",
                "searchable_message_projection_replay",
                "source_manifest_identity",
            ],
        )
