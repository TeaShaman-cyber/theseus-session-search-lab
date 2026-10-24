from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .artifact_overlap import load_accepted_ledger, set_digest
from .shard_allocation import ADAPTER_PROVIDERS


MATERIALIZER_CONTRACT_VERSION = "neon-shard-materializer-v1"
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
    rows = con.execute(
        "SELECT DISTINCT source_adapter FROM artifacts ORDER BY source_adapter"
    ).fetchall()
    adapters = {str(row[0]) for row in rows}
    if not adapters:
        raise ValueError("SOURCE_ARTIFACTS_EMPTY")
    resolved = set()
    for adapter in adapters:
        mapped = ADAPTER_PROVIDERS.get(adapter)
        if mapped is None:
            raise ValueError(f"SOURCE_PROVIDER_UNRESOLVED:{adapter}")
        resolved.add(mapped)
    if resolved != {provider}:
        raise ValueError("SOURCE_PROVIDER_MISMATCH")
    return adapters


def load_source_rows(corpus_root: Path, *, provider: str):
    con = _source_connection(corpus_root)
    try:
        _validate_source_provider(con, provider)
        sessions = [
            (str(r[0]), str(r[1]), str(r[2]), r[3], r[4])
            for r in con.execute(
                "SELECT session_id,title,coverage_state,first_message_time,last_message_time "
                "FROM sessions ORDER BY session_id"
            )
        ]
        if not sessions:
            raise ValueError("SOURCE_SESSIONS_EMPTY")
        messages = []
        seen_target_ids = set()
        placeholders = ",".join("?" for _ in SEARCHABLE_CLASSES)
        q = (
            "SELECT session_id,ordinal,message_id,local_identity,role,search_class,create_time,text "
            f"FROM messages WHERE search_class IN ({placeholders}) "
            "ORDER BY session_id,ordinal,row_id"
        )
        for r in con.execute(q, SEARCHABLE_CLASSES):
            sid = str(r[0])
            mid = r[2] if r[2] is not None else r[3]
            if mid is None or str(mid) == "":
                raise ValueError("TARGET_MESSAGE_ID_MISSING")
            mid = str(mid)
            key = (sid, mid)
            if key in seen_target_ids:
                raise ValueError("TARGET_MESSAGE_ID_COLLISION")
            seen_target_ids.add(key)
            messages.append((sid, int(r[1]), mid, str(r[4]), str(r[5]), r[6], str(r[7])))
        if not messages:
            raise ValueError("SOURCE_SEARCHABLE_MESSAGES_EMPTY")
        return sessions, messages
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


def build_source_manifest(corpus_root: Path, *, provider: str, shard_id: str) -> dict[str, Any]:
    corpus_root = Path(corpus_root)
    accepted = load_accepted_ledger(corpus_root)
    sessions, messages = load_source_rows(corpus_root, provider=provider)
    con = _source_connection(corpus_root)
    try:
        database_artifacts = {str(r[0]) for r in con.execute("SELECT sha256 FROM artifacts ORDER BY sha256")}
        adapters = [str(r[0]) for r in con.execute("SELECT DISTINCT source_adapter FROM artifacts ORDER BY source_adapter")]
    finally:
        con.close()
    if database_artifacts != accepted:
        raise ValueError("ACCEPTED_ARTIFACT_SET_MISMATCH")
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


def load_building_generation(
    corpus_root: Path, *, provider: str, shard_id: str, database_url: str, batch_size: int = 500
) -> dict[str, Any]:
    if not database_url:
        raise ValueError("DATABASE_URL_MISSING")
    if batch_size < 1 or batch_size > 5000:
        raise ValueError("BATCH_SIZE_OUT_OF_BOUNDS")
    manifest = build_source_manifest(corpus_root, provider=provider, shard_id=shard_id)
    sessions, messages = load_source_rows(corpus_root, provider=provider)

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
