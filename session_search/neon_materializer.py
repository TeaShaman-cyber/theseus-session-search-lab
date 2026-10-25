from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .artifact_overlap import load_accepted_ledger, set_digest
from .shard_allocation import ADAPTER_PROVIDERS, allocate_sessions, load_session_rows
from .corpus_store import CorpusMutationLock, CorpusPaths, verify_corpus


MATERIALIZER_CONTRACT_VERSION = "neon-shard-materializer-v2"
PROJECTION_SCHEMA_VERSION = "session-search-federated-v1"
SEARCHABLE_CLASSES = ("dialogue", "evidence", "trace")


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _source_connection(corpus_root: Path) -> sqlite3.Connection:
    database = Path(corpus_root) / "corpus.sqlite3"
    if not database.is_file():
        raise ValueError("SOURCE_CORPUS_DATABASE_MISSING")
    return sqlite3.connect(f"file:{database}?mode=ro", uri=True)


def _validate_source_provider(con: sqlite3.Connection, provider: str) -> set[str]:
    rows = con.execute("SELECT DISTINCT source_adapter FROM artifacts ORDER BY source_adapter").fetchall()
    adapters = {str(row[0]) for row in rows}
    if not adapters:
        raise ValueError("SOURCE_ARTIFACTS_EMPTY")
    resolved = {ADAPTER_PROVIDERS[a] for a in adapters if a in ADAPTER_PROVIDERS}
    for adapter in adapters:
        if adapter not in ADAPTER_PROVIDERS:
            raise ValueError(f"SOURCE_PROVIDER_UNRESOLVED:{adapter}")
    if resolved != {provider}:
        raise ValueError("SOURCE_PROVIDER_MISMATCH")
    return adapters


def _select_shard_assignment(con: sqlite3.Connection, *, provider: str, shard_id: str):
    allocation = allocate_sessions(load_session_rows(con))
    matches = [x for x in allocation["shards"] if x["shard_id"] == shard_id]
    if len(matches) != 1:
        raise ValueError("SOURCE_SHARD_NOT_FOUND")
    shard = matches[0]
    if shard["provider"] != provider:
        raise ValueError("SOURCE_SHARD_PROVIDER_MISMATCH")
    session_ids = tuple(str(x) for x in shard["session_ids"])
    if not session_ids:
        raise ValueError("SOURCE_SHARD_EMPTY")
    marks = ",".join("?" for _ in session_ids)
    adapters = [str(r[0]) for r in con.execute(
        "SELECT DISTINCT a.source_adapter FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id "
        f"WHERE r.projected_session_id IN ({marks}) ORDER BY a.source_adapter", session_ids
    )]
    if not adapters:
        raise ValueError("SOURCE_SHARD_PROVENANCE_MISSING")
    return shard, session_ids, adapters


def _load_rows_for_sessions(con: sqlite3.Connection, session_ids: tuple[str, ...]):
    marks = ",".join("?" for _ in session_ids)
    sessions = [(str(r[0]), str(r[1]), str(r[2]), r[3], r[4]) for r in con.execute(
        "SELECT session_id,title,coverage_state,first_message_time,last_message_time "
        f"FROM sessions WHERE session_id IN ({marks}) ORDER BY session_id", session_ids
    )]
    if not sessions:
        raise ValueError("SOURCE_SESSIONS_EMPTY")
    classes = ",".join("?" for _ in SEARCHABLE_CLASSES)
    q = ("SELECT session_id,ordinal,message_id,local_identity,role,search_class,create_time,text "
         f"FROM messages WHERE session_id IN ({marks}) AND search_class IN ({classes}) "
         "ORDER BY session_id,ordinal,row_id")
    messages=[]; seen=set()
    for r in con.execute(q, (*session_ids, *SEARCHABLE_CLASSES)):
        sid=str(r[0]); mid=r[2] if r[2] is not None else r[3]
        if mid is None or str(mid)=="": raise ValueError("TARGET_MESSAGE_ID_MISSING")
        mid=str(mid); key=(sid,mid)
        if key in seen: raise ValueError("TARGET_MESSAGE_ID_COLLISION")
        seen.add(key)
        messages.append((sid,int(r[1]),mid,str(r[4]),str(r[5]),r[6],str(r[7])))
    if not messages:
        raise ValueError("SOURCE_SEARCHABLE_MESSAGES_EMPTY")
    return sessions, messages


def load_source_rows(corpus_root: Path, *, provider: str, shard_id: str | None = None):
    con = _source_connection(corpus_root)
    try:
        if shard_id is None:
            _validate_source_provider(con, provider)
            session_ids = tuple(str(r[0]) for r in con.execute("SELECT session_id FROM sessions ORDER BY session_id"))
        else:
            _shard, session_ids, _adapters = _select_shard_assignment(con, provider=provider, shard_id=shard_id)
        return _load_rows_for_sessions(con, session_ids)
    finally:
        con.close()

def _projection_source_digest(sessions, messages) -> str:
    h = hashlib.sha256()
    for kind, rows in (("session", sessions), ("message", messages)):
        for row in rows:
            payload = json.dumps([kind, *row], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            h.update(payload)
            h.update(b"\n")
    return h.hexdigest()


def _manifest_from_snapshot(
    accepted: set[str],
    sessions,
    messages,
    adapters: list[str],
    *,
    provider: str,
    shard_id: str,
) -> dict[str, Any]:
    core = {
        "materializer_contract_version": MATERIALIZER_CONTRACT_VERSION,
        "projection_schema_version": PROJECTION_SCHEMA_VERSION,
        "provider": provider,
        "shard_id": shard_id,
        "searchable_classes": list(SEARCHABLE_CLASSES),
        "accepted_artifact_count": len(accepted),
        "accepted_set_sha256": set_digest(accepted),
        "source_projection_sha256": _projection_source_digest(sessions, messages),
        "source_adapters": adapters,
        "session_count": len(sessions),
        "message_count": len(messages),
        "searchable_text_bytes": sum(len(r[6].encode("utf-8")) for r in messages),
    }
    source_manifest_sha256 = _canonical_sha256(core)
    generation_id = hashlib.sha256(
        ("session-search-generation-v1\n" + source_manifest_sha256).encode("ascii")
    ).hexdigest()
    return {**core, "source_manifest_sha256": source_manifest_sha256, "generation_id": generation_id}


def _capture_source_snapshot(
    corpus_root: Path,
    *,
    provider: str,
    shard_id: str,
    require_verified: bool = False,
):
    corpus_root = Path(corpus_root)
    paths = CorpusPaths.from_root(corpus_root)
    with CorpusMutationLock(paths, "neon-materialize-snapshot"):
        verification = None
        if require_verified:
            verification = verify_corpus(corpus_root)
            if verification.get("status") != "VERIFIED":
                raise ValueError("SOURCE_CORPUS_NOT_VERIFIED")

        accepted = load_accepted_ledger(corpus_root)
        con = _source_connection(corpus_root)
        try:
            database_artifacts = {str(r[0]) for r in con.execute("SELECT sha256 FROM artifacts ORDER BY sha256")}
            shard, session_ids, adapters = _select_shard_assignment(con, provider=provider, shard_id=shard_id)
            sessions, messages = _load_rows_for_sessions(con, session_ids)
        finally:
            con.close()

        if database_artifacts != accepted:
            raise ValueError("ACCEPTED_ARTIFACT_SET_MISMATCH")
        if len(sessions) != int(shard["sessions"]):
            raise ValueError("SOURCE_SHARD_SESSION_COUNT_MISMATCH")
        if len(messages) != int(shard["searchable_messages"]):
            raise ValueError("SOURCE_SHARD_MESSAGE_COUNT_MISMATCH")
        if sum(len(r[6].encode("utf-8")) for r in messages) != int(shard["searchable_text_bytes"]):
            raise ValueError("SOURCE_SHARD_TEXT_BYTES_MISMATCH")
        if verification is not None:
            verified_sessions = verification.get("sessions")
            if not isinstance(verified_sessions, int) or verified_sessions < len(sessions):
                raise ValueError("SOURCE_VERIFICATION_SESSION_COUNT_MISMATCH")
            verified_messages = verification.get("messages")
            if not isinstance(verified_messages, int) or verified_messages < len(messages):
                raise ValueError("SOURCE_VERIFICATION_MESSAGE_COUNT_MISMATCH")

        manifest = _manifest_from_snapshot(
            accepted, sessions, messages, adapters, provider=provider, shard_id=shard_id
        )
        return manifest, sessions, messages


def build_source_manifest(corpus_root: Path, *, provider: str, shard_id: str) -> dict[str, Any]:
    manifest, _sessions, _messages = _capture_source_snapshot(
        corpus_root, provider=provider, shard_id=shard_id
    )
    return manifest


def load_building_generation(
    corpus_root: Path, *, provider: str, shard_id: str, database_url: str, batch_size: int = 500
) -> dict[str, Any]:
    if not database_url:
        raise ValueError("DATABASE_URL_MISSING")
    if batch_size < 1 or batch_size > 5000:
        raise ValueError("BATCH_SIZE_OUT_OF_BOUNDS")
    manifest, sessions, messages = _capture_source_snapshot(
        corpus_root, provider=provider, shard_id=shard_id, require_verified=True
    )

    import psycopg2
    from psycopg2.extras import execute_values

    conn = psycopg2.connect(database_url)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO session_search.generations("
                    "generation_id,shard_id,provider,source_manifest_sha256,projection_schema_version"
                    ") VALUES (%s,%s,%s,%s,%s)",
                    (
                        manifest["generation_id"],
                        shard_id,
                        provider,
                        manifest["source_manifest_sha256"],
                        PROJECTION_SCHEMA_VERSION,
                    ),
                )
                execute_values(
                    cur,
                    "INSERT INTO session_search.sessions("
                    "generation_id,session_id,title,coverage_state,time_min,time_max"
                    ") VALUES %s",
                    [(manifest["generation_id"], *r) for r in sessions],
                    page_size=batch_size,
                )
                for start in range(0, len(messages), batch_size):
                    page = [(manifest["generation_id"], *r) for r in messages[start:start+batch_size]]
                    execute_values(
                        cur,
                        "INSERT INTO session_search.messages("
                        "generation_id,session_id,ordinal,message_id,role,search_class,create_time,text"
                        ") VALUES %s",
                        page,
                        page_size=batch_size,
                    )
                cur.execute(
                    "SELECT "
                    "(SELECT state FROM session_search.generations WHERE generation_id=%s),"
                    "(SELECT count(*) FROM session_search.sessions WHERE generation_id=%s),"
                    "(SELECT count(*) FROM session_search.messages WHERE generation_id=%s)",
                    (manifest["generation_id"], manifest["generation_id"], manifest["generation_id"]),
                )
                state, session_count, message_count = cur.fetchone()
                if state != "BUILDING":
                    raise RuntimeError("TARGET_GENERATION_NOT_BUILDING")
                if int(session_count) != manifest["session_count"]:
                    raise RuntimeError("TARGET_SESSION_COUNT_MISMATCH")
                if int(message_count) != manifest["message_count"]:
                    raise RuntimeError("TARGET_MESSAGE_COUNT_MISMATCH")
    finally:
        conn.close()
    return {
        **manifest,
        "target_state": "BUILDING",
        "loaded_sessions": manifest["session_count"],
        "loaded_messages": manifest["message_count"],
    }
