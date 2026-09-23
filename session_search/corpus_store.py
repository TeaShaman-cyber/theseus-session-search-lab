from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import shutil
import socket
import sqlite3
import tempfile
import uuid
import zipfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from .artifact import NormalizedArtifact, file_sha256, normalize_artifact
from .branch_routing import (
    ROUTING_VERSION,
    ProjectionRoute,
    build_official_families,
    route_artifact,
)

CORPUS_SCHEMA_VERSION = "session-search-corpus-v2"
ACCEPTED_LEDGER_SCHEMA = "theseus.session-search-accepted-artifact.v1"


@dataclasses.dataclass(frozen=True)
class CorpusPaths:
    root: pathlib.Path
    artifacts_sha256: pathlib.Path
    accepted_ledger: pathlib.Path
    ingest_receipts: pathlib.Path
    rebuild_receipts: pathlib.Path
    staging: pathlib.Path
    mutation_lock: pathlib.Path
    db: pathlib.Path

    @classmethod
    def from_root(cls, root: pathlib.Path) -> "CorpusPaths":
        root = pathlib.Path(root).expanduser()
        return cls(
            root=root,
            artifacts_sha256=root / "artifacts" / "sha256",
            accepted_ledger=root / "ledger" / "accepted",
            ingest_receipts=root / "receipts" / "ingest",
            rebuild_receipts=root / "receipts" / "rebuild",
            staging=root / "staging",
            mutation_lock=root / "mutation.lock",
            db=root / "corpus.sqlite3",
        )

    def ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in (
            self.artifacts_sha256,
            self.accepted_ledger,
            self.ingest_receipts,
            self.rebuild_receipts,
            self.staging,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def resolve_corpus_root(
    explicit: str | None,
    env: Mapping[str, str] | None = None,
) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit).expanduser()
    values = os.environ if env is None else env
    configured = values.get("SESSION_SEARCH_CORPUS")
    if configured:
        return pathlib.Path(configured).expanduser()
    raise ValueError("CORPUS_LOCATION_UNRESOLVED: use --corpus or SESSION_SEARCH_CORPUS")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CorpusMutationLock:
    def __init__(self, paths: CorpusPaths, operation: str):
        self.paths = paths
        self.operation = operation
        self.token = uuid.uuid4().hex
        self._owned = False

    @property
    def owner_path(self) -> pathlib.Path:
        return self.paths.mutation_lock / "owner.json"

    def __enter__(self) -> "CorpusMutationLock":
        self.paths.ensure_layout()
        try:
            self.paths.mutation_lock.mkdir()
        except FileExistsError as exc:
            raise RuntimeError("CORPUS_MUTATION_LOCKED") from exc
        try:
            owner = {
                "schema": "theseus.session-search-corpus-lock.v1",
                "token": self.token,
                "operation": self.operation,
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "started_at": _utc_now(),
            }
            self.owner_path.write_bytes(_stable_json_bytes(owner))
            self._owned = True
            return self
        except Exception:
            try:
                self.paths.mutation_lock.rmdir()
            except OSError:
                pass
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._owned:
            return
        current = None
        try:
            current = json.loads(self.owner_path.read_text())
        except Exception:
            current = None
        if isinstance(current, dict) and current.get("token") == self.token:
            try:
                self.owner_path.unlink()
            finally:
                self.paths.mutation_lock.rmdir()
        self._owned = False


def accepted_entry_path(paths: CorpusPaths, sha256: str) -> pathlib.Path:
    return paths.accepted_ledger / f"{sha256}.json"


def accepted_entry_digest(entry: dict) -> str:
    return _sha256_bytes(_stable_json_bytes(entry))


def _validate_accepted_entry(entry: dict) -> None:
    if entry.get("schema") != ACCEPTED_LEDGER_SCHEMA:
        raise ValueError("invalid accepted-ledger schema")
    sha = entry.get("artifact_sha256")
    if not isinstance(sha, str) or len(sha) != 64:
        raise ValueError("invalid accepted artifact sha256")
    if not isinstance(entry.get("size_bytes"), int) or int(entry["size_bytes"]) < 0:
        raise ValueError("invalid accepted artifact size")
    if not entry.get("session_id"):
        raise ValueError("invalid accepted session identity")


def write_accepted_entry(paths: CorpusPaths, entry: dict) -> pathlib.Path:
    paths.ensure_layout()
    _validate_accepted_entry(entry)
    sha = str(entry["artifact_sha256"])
    target = accepted_entry_path(paths, sha)
    data = _stable_json_bytes(entry)
    if target.exists():
        existing = target.read_bytes()
        if existing != data:
            raise RuntimeError("ACCEPTED_LEDGER_CONFLICT")
        return target
    temp = paths.staging / f"accepted-{sha}-{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, target)
        observed = json.loads(target.read_text())
        _validate_accepted_entry(observed)
        if observed != entry:
            raise RuntimeError("ACCEPTED_LEDGER_READBACK_MISMATCH")
        return target
    finally:
        if temp.exists():
            temp.unlink()


def read_accepted_ledger(paths: CorpusPaths) -> dict[str, dict]:
    paths.ensure_layout()
    result: dict[str, dict] = {}
    for path in sorted(paths.accepted_ledger.glob("*.json")):
        entry = json.loads(path.read_text())
        _validate_accepted_entry(entry)
        sha = str(entry["artifact_sha256"])
        if path.name != f"{sha}.json":
            raise RuntimeError("ACCEPTED_LEDGER_PATH_MISMATCH")
        if sha in result:
            raise RuntimeError("ACCEPTED_LEDGER_DUPLICATE")
        result[sha] = entry
    return result


def init_corpus_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE corpus_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO corpus_meta(key,value) VALUES ('schema_version','session-search-corpus-v2');

        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            coverage_state TEXT NOT NULL,
            coverage_reason TEXT NOT NULL,
            first_message_time REAL,
            last_message_time REAL,
            first_accepted_at TEXT,
            last_accepted_at TEXT,
            title_source_time REAL,
            title_source_artifact_sha256 TEXT
        );

        CREATE TABLE artifacts (
            artifact_id INTEGER PRIMARY KEY,
            sha256 TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            source_schema TEXT NOT NULL,
            source_adapter TEXT NOT NULL,
            original_filename TEXT,
            observed_title TEXT NOT NULL,
            accepted_at TEXT NOT NULL,
            coverage_state TEXT NOT NULL,
            session_id TEXT NOT NULL,
            ledger_sha256 TEXT NOT NULL,
            observed_min_time REAL,
            observed_max_time REAL,
            observed_message_count INTEGER NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        );

        CREATE TABLE artifact_routes (
            artifact_id INTEGER PRIMARY KEY,
            accepted_session_id TEXT NOT NULL,
            projected_session_id TEXT NOT NULL,
            route_state TEXT NOT NULL,
            route_reason TEXT NOT NULL,
            route_version TEXT NOT NULL,
            FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
        );
        CREATE INDEX artifact_routes_projected_session
        ON artifact_routes(projected_session_id);

        CREATE TABLE payload_pages (
            page_id INTEGER PRIMARY KEY,
            artifact_id INTEGER NOT NULL,
            session_id TEXT NOT NULL,
            capture_sequence INTEGER NOT NULL,
            member_name TEXT NOT NULL,
            start_cursor TEXT,
            end_cursor TEXT,
            has_previous_page INTEGER NOT NULL,
            has_next_page INTEGER NOT NULL,
            message_count INTEGER NOT NULL,
            min_create_time REAL,
            max_create_time REAL,
            UNIQUE(artifact_id, member_name),
            FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id),
            FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        );

        CREATE TABLE messages (
            row_id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            message_id TEXT,
            local_identity TEXT NOT NULL,
            canonical_message_sha256 TEXT NOT NULL,
            role TEXT NOT NULL,
            content_type TEXT NOT NULL,
            search_class TEXT NOT NULL,
            create_time REAL,
            provider_order INTEGER,
            text TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        );
        CREATE UNIQUE INDEX messages_identified_identity
        ON messages(session_id, message_id)
        WHERE message_id IS NOT NULL;
        CREATE UNIQUE INDEX messages_local_identity ON messages(session_id, local_identity);
        CREATE INDEX messages_session_ordinal ON messages(session_id, ordinal);

        CREATE TABLE message_sources (
            message_row_id INTEGER NOT NULL,
            page_id INTEGER NOT NULL,
            page_position INTEGER NOT NULL,
            source_message_id TEXT,
            source_object_sha256 TEXT NOT NULL,
            PRIMARY KEY (message_row_id, page_id, page_position),
            FOREIGN KEY(message_row_id) REFERENCES messages(row_id),
            FOREIGN KEY(page_id) REFERENCES payload_pages(page_id)
        );

        CREATE VIRTUAL TABLE messages_fts USING fts5(
            text,
            content=''
        );
        """
    )



def _artifact_blob_path(paths: CorpusPaths, sha256: str) -> pathlib.Path:
    return paths.artifacts_sha256 / f"{sha256}.zip"


def _copy_artifact_blob(paths: CorpusPaths, artifact: NormalizedArtifact) -> pathlib.Path:
    paths.ensure_layout()
    target = _artifact_blob_path(paths, artifact.artifact_sha256)
    if target.exists():
        if target.stat().st_size != artifact.size_bytes or file_sha256(target) != artifact.artifact_sha256:
            raise RuntimeError("FAILED_INTEGRITY: existing artifact blob mismatch")
        return target
    temp = paths.staging / f"artifact-{artifact.artifact_sha256}-{uuid.uuid4().hex}.tmp"
    try:
        with artifact.source.open("rb") as src, temp.open("xb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        if temp.stat().st_size != artifact.size_bytes or file_sha256(temp) != artifact.artifact_sha256:
            raise RuntimeError("FAILED_INTEGRITY: staged artifact mismatch")
        os.replace(temp, target)
        if file_sha256(target) != artifact.artifact_sha256:
            raise RuntimeError("FAILED_INTEGRITY: stored artifact mismatch")
        return target
    finally:
        if temp.exists():
            temp.unlink()


def _connect_corpus(paths: CorpusPaths) -> sqlite3.Connection:
    paths.ensure_layout()
    new_db = not paths.db.exists()
    conn = sqlite3.connect(paths.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if new_db:
        init_corpus_db(conn)
        conn.commit()
    else:
        row = conn.execute("SELECT value FROM corpus_meta WHERE key='schema_version'").fetchone()
        if row is None or row[0] != CORPUS_SCHEMA_VERSION:
            conn.close()
            raise RuntimeError("CORPUS_SCHEMA_MISMATCH")
        route_table = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='artifact_routes'"
        ).fetchone()[0]
        if int(route_table) != 1:
            conn.close()
            raise RuntimeError("CORPUS_SCHEMA_MISMATCH")
        columns={r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        if "provider_order" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN provider_order INTEGER")
            conn.commit()
    return conn


def _artifact_time_bounds(artifact: NormalizedArtifact) -> tuple[float | None, float | None]:
    values = [m.create_time for m in artifact.messages if m.create_time is not None]
    return (min(values), max(values)) if values else (None, None)


def _accepted_entry_for_artifact(artifact: NormalizedArtifact, accepted_at: str) -> dict:
    return {
        "schema": ACCEPTED_LEDGER_SCHEMA,
        "artifact_sha256": artifact.artifact_sha256,
        "size_bytes": artifact.size_bytes,
        "session_id": artifact.session_id,
        "coverage_state": artifact.coverage_state,
        "source_schema": artifact.source_schema,
        "source_adapter": artifact.source_adapter,
        "accepted_at": accepted_at,
    }


def _read_one_accepted_entry(paths: CorpusPaths, sha256: str) -> dict | None:
    path = accepted_entry_path(paths, sha256)
    if not path.exists():
        return None
    entry = json.loads(path.read_text())
    _validate_accepted_entry(entry)
    if entry["artifact_sha256"] != sha256:
        raise RuntimeError("ACCEPTED_LEDGER_PATH_MISMATCH")
    return entry


def _membership_state(conn: sqlite3.Connection, paths: CorpusPaths, sha256: str) -> tuple[dict | None, sqlite3.Row | None]:
    ledger = _read_one_accepted_entry(paths, sha256)
    db_row = conn.execute("SELECT * FROM artifacts WHERE sha256=?", (sha256,)).fetchone()
    return ledger, db_row


def _full_sqlite_integrity(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def _verify_existing_membership(
    conn: sqlite3.Connection,
    paths: CorpusPaths,
    artifact: NormalizedArtifact,
    ledger: dict,
    db_row: sqlite3.Row,
    *,
    check_sqlite_integrity: bool = True,
) -> dict:
    blob = _artifact_blob_path(paths, artifact.artifact_sha256)
    if not blob.exists() or blob.stat().st_size != artifact.size_bytes or file_sha256(blob) != artifact.artifact_sha256:
        raise RuntimeError("RECONCILIATION_REQUIRED: accepted artifact blob mismatch")
    if int(ledger["size_bytes"]) != artifact.size_bytes or ledger["session_id"] != artifact.session_id:
        raise RuntimeError("RECONCILIATION_REQUIRED: ledger metadata mismatch")
    if db_row["session_id"] != artifact.session_id or int(db_row["size_bytes"]) != artifact.size_bytes:
        raise RuntimeError("RECONCILIATION_REQUIRED: projection metadata mismatch")
    if db_row["ledger_sha256"] != accepted_entry_digest(ledger):
        raise RuntimeError("RECONCILIATION_REQUIRED: projection ledger digest mismatch")
    route = conn.execute(
        "SELECT * FROM artifact_routes WHERE artifact_id=?",
        (db_row["artifact_id"],),
    ).fetchone()
    if route is None or route["accepted_session_id"] != artifact.session_id or route["route_version"] != ROUTING_VERSION:
        raise RuntimeError("RECONCILIATION_REQUIRED: projection route mismatch")
    if check_sqlite_integrity and _full_sqlite_integrity(conn) != "ok":
        raise RuntimeError("RECONCILIATION_REQUIRED: sqlite integrity failure")
    return {
        "status": "ALREADY_INGESTED",
        "mutation": "none",
        "artifact_sha256": artifact.artifact_sha256,
        "session_id": artifact.session_id,
        "projected_session_id": str(route["projected_session_id"]),
        "route_state": str(route["route_state"]),
    }


def _ensure_session_stub(
    conn: sqlite3.Connection,
    session_id: str,
    artifact: NormalizedArtifact,
    accepted_at: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO sessions(
            session_id,title,coverage_state,coverage_reason,
            first_message_time,last_message_time,first_accepted_at,last_accepted_at,
            title_source_time,title_source_artifact_sha256
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            session_id,
            artifact.title,
            "PARTIAL_SESSION_SLICE",
            "pending_recompute",
            None,
            None,
            accepted_at,
            accepted_at,
            None,
            artifact.artifact_sha256,
        ),
    )


def _insert_artifact_row(conn: sqlite3.Connection, artifact: NormalizedArtifact, accepted_at: str) -> int:
    min_time, max_time = _artifact_time_bounds(artifact)
    entry = _accepted_entry_for_artifact(artifact, accepted_at)
    cur = conn.execute(
        """
        INSERT INTO artifacts(
            sha256,size_bytes,source_schema,source_adapter,original_filename,observed_title,
            accepted_at,coverage_state,session_id,ledger_sha256,
            observed_min_time,observed_max_time,observed_message_count
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            artifact.artifact_sha256,
            artifact.size_bytes,
            artifact.source_schema,
            artifact.source_adapter,
            artifact.source.name,
            artifact.title,
            accepted_at,
            artifact.coverage_state,
            artifact.session_id,
            accepted_entry_digest(entry),
            min_time,
            max_time,
            len(artifact.messages),
        ),
    )
    return int(cur.lastrowid)


def _insert_route_row(
    conn: sqlite3.Connection,
    artifact_id: int,
    route: ProjectionRoute,
) -> None:
    conn.execute(
        """
        INSERT INTO artifact_routes(
            artifact_id,accepted_session_id,projected_session_id,route_state,route_reason,route_version
        ) VALUES (?,?,?,?,?,?)
        """,
        (
            artifact_id,
            route.accepted_session_id,
            route.projected_session_id,
            route.state,
            route.reason,
            ROUTING_VERSION,
        ),
    )


def _insert_pages(
    conn: sqlite3.Connection,
    artifact: NormalizedArtifact,
    artifact_id: int,
    projected_session_id: str,
) -> dict[int, int]:
    page_ids: dict[int, int] = {}
    for page in artifact.pages:
        cur = conn.execute(
            """
            INSERT INTO payload_pages(
                artifact_id,session_id,capture_sequence,member_name,start_cursor,end_cursor,
                has_previous_page,has_next_page,message_count,min_create_time,max_create_time
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                artifact_id,
                projected_session_id,
                page.capture_sequence,
                page.member_name,
                page.start_cursor,
                page.end_cursor,
                int(page.has_previous_page),
                int(page.has_next_page),
                page.message_count,
                page.min_create_time,
                page.max_create_time,
            ),
        )
        page_ids[page.capture_sequence] = int(cur.lastrowid)
    return page_ids


def _deterministic_local_identity(artifact: NormalizedArtifact, message) -> str:
    if message.message_id is not None:
        return f"id:{message.message_id}"
    source = min(message.sources, key=lambda s: (s.capture_sequence, s.page_position, s.member_name))
    return f"anon:{artifact.artifact_sha256}:{source.capture_sequence}:{source.page_position}"


def _existing_projection_source_digests(
    conn: sqlite3.Connection,
    paths: CorpusPaths,
    row: sqlite3.Row,
    cache: dict[tuple[int, str], set[str]],
) -> set[str]:
    key = (int(row["row_id"]), str(row["canonical_message_sha256"]))
    if key in cache:
        return cache[key]
    digests: set[str] = set()
    source_rows = conn.execute(
        """
        SELECT DISTINCT a.sha256
        FROM message_sources ms
        JOIN payload_pages p ON p.page_id=ms.page_id
        JOIN artifacts a ON a.artifact_id=p.artifact_id
        WHERE ms.message_row_id=? AND a.source_adapter=?
        ORDER BY a.sha256
        """,
        (row["row_id"], "chatgpt-export"),
    ).fetchall()
    for source_row in source_rows:
        source_artifact = normalize_artifact(_artifact_blob_path(paths, str(source_row["sha256"])))
        for candidate in source_artifact.messages:
            if (
                candidate.message_id == row["message_id"]
                and candidate.canonical_message_sha256 == row["canonical_message_sha256"]
                and candidate.projection_source_canonical_sha256 is not None
            ):
                digests.add(candidate.projection_source_canonical_sha256)
    cache[key] = digests
    return digests


def _linked_projection_resolution(
    conn: sqlite3.Connection,
    paths: CorpusPaths,
    artifact: NormalizedArtifact,
    message,
    row: sqlite3.Row,
    cache: dict[tuple[int, str], set[str]],
) -> str | None:
    same_role = row["role"] == message.role
    incoming_projection = (
        artifact.source_adapter == "chatgpt-export"
        and same_role
        and row["content_type"] == "multimodal_text"
        and row["search_class"] == "trace"
        and message.content_type == "text"
        and message.search_class == "dialogue"
        and message.projection_source_canonical_sha256 == row["canonical_message_sha256"]
    )
    if incoming_projection:
        return "promote"

    incoming_raw = (
        same_role
        and row["content_type"] == "text"
        and row["search_class"] == "dialogue"
        and message.content_type == "multimodal_text"
        and message.search_class == "trace"
    )
    if incoming_raw:
        source_digests = _existing_projection_source_digests(conn, paths, row, cache)
        if message.canonical_message_sha256 in source_digests:
            return "keep"
    return None


def _promote_linked_projection(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    message,
) -> None:
    if row["text"] != "":
        raise RuntimeError("FAILED_CONFLICTING_DUPLICATE")
    fts_rows = int(conn.execute("SELECT count(*) FROM messages_fts WHERE rowid=?", (row["row_id"],)).fetchone()[0])
    if fts_rows != 0:
        raise RuntimeError("FAILED_CONFLICTING_DUPLICATE")
    conn.execute(
        """
        UPDATE messages
        SET canonical_message_sha256=?,role=?,content_type=?,search_class=?,text=?
        WHERE row_id=?
        """,
        (
            message.canonical_message_sha256,
            message.role,
            message.content_type,
            message.search_class,
            message.text,
            row["row_id"],
        ),
    )
    if message.text and message.search_class != "hidden":
        conn.execute("INSERT INTO messages_fts(rowid,text) VALUES (?,?)", (row["row_id"], message.text))


def _upsert_messages(
    conn: sqlite3.Connection,
    paths: CorpusPaths,
    artifact: NormalizedArtifact,
    page_ids: dict[int, int],
    projected_session_id: str,
) -> dict:
    novel = 0
    reused = 0
    source_additions = 0
    projection_digest_cache: dict[tuple[int, str], set[str]] = {}
    for message in artifact.messages:
        row = None
        if message.message_id is not None:
            row = conn.execute(
                "SELECT * FROM messages WHERE session_id=? AND message_id=?",
                (projected_session_id, message.message_id),
            ).fetchone()
        if row is None and artifact.source_adapter == "speed-booster-export" and message.provider_order is not None:
            order_rows = conn.execute(
                """
                SELECT DISTINCT m.*
                FROM messages m
                JOIN message_sources ms ON ms.message_row_id=m.row_id
                JOIN payload_pages p ON p.page_id=ms.page_id
                JOIN artifacts a ON a.artifact_id=p.artifact_id
                WHERE m.session_id=? AND m.provider_order=? AND a.source_adapter=?
                ORDER BY m.row_id
                """,
                (projected_session_id, message.provider_order, "speed-booster-export"),
            ).fetchall()
            if len(order_rows) > 1:
                raise RuntimeError("FAILED_CONFLICTING_DUPLICATE")
            if order_rows:
                row = order_rows[0]
                if row["message_id"] != message.message_id:
                    raise RuntimeError("FAILED_CONFLICTING_DUPLICATE")
                if row["canonical_message_sha256"] != message.canonical_message_sha256:
                    raise RuntimeError("FAILED_CONFLICTING_DUPLICATE")
        if row is None:
            local_identity = _deterministic_local_identity(artifact, message)
            cur = conn.execute(
                """
                INSERT INTO messages(
                    session_id,ordinal,message_id,local_identity,canonical_message_sha256,
                    role,content_type,search_class,create_time,provider_order,text
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    projected_session_id,
                    0,
                    message.message_id,
                    local_identity,
                    message.canonical_message_sha256,
                    message.role,
                    message.content_type,
                    message.search_class,
                    message.create_time,
                    message.provider_order,
                    message.text,
                ),
            )
            row_id = int(cur.lastrowid)
            if message.text and message.search_class != "hidden":
                conn.execute("INSERT INTO messages_fts(rowid,text) VALUES (?,?)", (row_id, message.text))
            novel += 1
        else:
            if row["canonical_message_sha256"] == message.canonical_message_sha256 and message.projection_source_canonical_sha256 is not None:
                existing_lineages = _existing_projection_source_digests(conn, paths, row, projection_digest_cache)
                if existing_lineages and existing_lineages != {message.projection_source_canonical_sha256}:
                    raise RuntimeError("FAILED_CONFLICTING_DUPLICATE")
            if row["canonical_message_sha256"] != message.canonical_message_sha256:
                resolution = _linked_projection_resolution(
                    conn,
                    paths,
                    artifact,
                    message,
                    row,
                    projection_digest_cache,
                )
                if resolution == "promote":
                    _promote_linked_projection(conn, row, message)
                elif resolution != "keep":
                    raise RuntimeError("FAILED_CONFLICTING_DUPLICATE")
            row_id = int(row["row_id"])
            existing_time = row["create_time"]
            incoming_time = message.create_time
            existing_order = row["provider_order"] if "provider_order" in row.keys() else None
            incoming_order = message.provider_order
            if incoming_order is not None:
                merged_order = incoming_order if existing_order is None else max(int(existing_order), int(incoming_order))
                if existing_order != merged_order:
                    conn.execute("UPDATE messages SET provider_order=? WHERE row_id=?", (merged_order, row_id))
            if incoming_time is not None and (existing_time is None or float(incoming_time) < float(existing_time)):
                conn.execute("UPDATE messages SET create_time=? WHERE row_id=?", (incoming_time, row_id))
            reused += 1
        for source in message.sources:
            conn.execute(
                """
                INSERT INTO message_sources(
                    message_row_id,page_id,page_position,source_message_id,source_object_sha256
                ) VALUES (?,?,?,?,?)
                """,
                (
                    row_id,
                    page_ids[source.capture_sequence],
                    source.page_position,
                    message.message_id,
                    source.source_object_sha256,
                ),
            )
            source_additions += 1
    return {"novel_messages": novel, "reused_messages": reused, "provenance_additions": source_additions}


def _recompute_ordinals(conn: sqlite3.Connection, session_id: str) -> None:
    rows = conn.execute(
        """
        SELECT row_id FROM messages
        WHERE session_id=?
        ORDER BY (provider_order IS NULL), provider_order, (create_time IS NULL), create_time, local_identity
        """,
        (session_id,),
    ).fetchall()
    for ordinal, row in enumerate(rows):
        conn.execute("UPDATE messages SET ordinal=? WHERE row_id=?", (ordinal, int(row["row_id"])))


def _session_metadata_values(conn: sqlite3.Connection, session_id: str) -> dict:
    artifacts = conn.execute(
        """
        SELECT a.*
        FROM artifacts a
        JOIN artifact_routes r ON r.artifact_id=a.artifact_id
        WHERE r.projected_session_id=?
        ORDER BY a.sha256
        """,
        (session_id,),
    ).fetchall()
    total_messages = int(
        conn.execute("SELECT count(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0]
    )
    complete_cover = False
    for artifact in artifacts:
        if artifact["coverage_state"] != "COMPLETE_EXPOSED_CONVERSATION":
            continue
        covered = int(
            conn.execute(
                """
                SELECT count(DISTINCT ms.message_row_id)
                FROM message_sources ms
                JOIN payload_pages p ON p.page_id=ms.page_id
                JOIN messages m ON m.row_id=ms.message_row_id
                WHERE p.artifact_id=? AND m.session_id=?
                """,
                (artifact["artifact_id"], session_id),
            ).fetchone()[0]
        )
        if covered == total_messages:
            complete_cover = True
            break
    coverage = "COMPLETE_EXPOSED_CONVERSATION" if complete_cover else "PARTIAL_SESSION_SLICE"
    reason = (
        "explicit_complete_artifact_covers_all_messages"
        if complete_cover
        else "no_single_complete_artifact_covers_all_messages"
    )
    bounds = conn.execute(
        "SELECT min(create_time),max(create_time) FROM messages WHERE session_id=? AND create_time IS NOT NULL",
        (session_id,),
    ).fetchone()
    accepted_times = [str(row["accepted_at"]) for row in artifacts]
    if artifacts:
        timed = [row for row in artifacts if row["observed_max_time"] is not None]
        if timed:
            best_time = max(float(row["observed_max_time"]) for row in timed)
            candidates = [row for row in timed if float(row["observed_max_time"]) == best_time]
        else:
            best_time = None
            candidates = list(artifacts)
        chosen = sorted(candidates, key=lambda row: str(row["sha256"]))[0]
        title = str(chosen["observed_title"] or "")
        title_time = chosen["observed_max_time"]
        title_sha = str(chosen["sha256"])
    else:
        title = ""
        title_time = None
        title_sha = None
    return {
        "title": title,
        "coverage_state": coverage,
        "coverage_reason": reason,
        "first_message_time": bounds[0],
        "last_message_time": bounds[1],
        "first_accepted_at": min(accepted_times) if accepted_times else None,
        "last_accepted_at": max(accepted_times) if accepted_times else None,
        "title_source_time": title_time,
        "title_source_artifact_sha256": title_sha,
    }


def _recompute_session_metadata(conn: sqlite3.Connection, session_id: str) -> None:
    values = _session_metadata_values(conn, session_id)
    conn.execute(
        """
        UPDATE sessions SET
            title=?,coverage_state=?,coverage_reason=?,first_message_time=?,last_message_time=?,
            first_accepted_at=?,last_accepted_at=?,title_source_time=?,title_source_artifact_sha256=?
        WHERE session_id=?
        """,
        (
            values["title"],
            values["coverage_state"],
            values["coverage_reason"],
            values["first_message_time"],
            values["last_message_time"],
            values["first_accepted_at"],
            values["last_accepted_at"],
            values["title_source_time"],
            values["title_source_artifact_sha256"],
            session_id,
        ),
    )

def apply_normalized_artifact_to_projection(
    conn: sqlite3.Connection,
    paths: CorpusPaths,
    artifact: NormalizedArtifact,
    accepted_at: str,
    route: ProjectionRoute | None = None,
) -> dict:
    route = route or ProjectionRoute(
        accepted_session_id=artifact.session_id,
        projected_session_id=artifact.session_id,
        state="DIRECT",
        reason="explicit_session_identity",
    )
    if route.accepted_session_id != artifact.session_id:
        raise RuntimeError("FAILED_BRANCH_ROUTE_SOURCE_IDENTITY")
    _ensure_session_stub(conn, artifact.session_id, artifact, accepted_at)
    _ensure_session_stub(conn, route.projected_session_id, artifact, accepted_at)
    artifact_id = _insert_artifact_row(conn, artifact, accepted_at)
    _insert_route_row(conn, artifact_id, route)
    page_ids = _insert_pages(conn, artifact, artifact_id, route.projected_session_id)
    delta = _upsert_messages(conn, paths, artifact, page_ids, route.projected_session_id)
    _recompute_ordinals(conn, route.projected_session_id)
    _recompute_session_metadata(conn, route.projected_session_id)
    if route.projected_session_id != artifact.session_id:
        _recompute_session_metadata(conn, artifact.session_id)
    return {
        "artifact_id": artifact_id,
        "projected_session_id": route.projected_session_id,
        "route_state": route.state,
        **delta,
    }


def _assert_global_transaction_invariants(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("FAILED_TRANSACTION: foreign key invariant")
    duplicates = conn.execute(
        """
        SELECT session_id,message_id,count(*)
        FROM messages WHERE message_id IS NOT NULL
        GROUP BY session_id,message_id HAVING count(*)>1
        """
    ).fetchall()
    if duplicates:
        raise RuntimeError("FAILED_TRANSACTION: identified message uniqueness invariant")


def assert_transaction_invariants(
    conn: sqlite3.Connection,
    artifact: NormalizedArtifact,
    *,
    check_global: bool = True,
) -> None:
    row = conn.execute("SELECT count(*) FROM artifacts WHERE sha256=?", (artifact.artifact_sha256,)).fetchone()
    if int(row[0]) != 1:
        raise RuntimeError("FAILED_TRANSACTION: artifact registry invariant")
    route_row = conn.execute(
        """
        SELECT count(*) FROM artifact_routes r
        JOIN artifacts a ON a.artifact_id=r.artifact_id
        WHERE a.sha256=? AND r.accepted_session_id=? AND r.route_version=?
        """,
        (artifact.artifact_sha256, artifact.session_id, ROUTING_VERSION),
    ).fetchone()
    if int(route_row[0]) != 1:
        raise RuntimeError("FAILED_TRANSACTION: artifact route invariant")
    if check_global:
        _assert_global_transaction_invariants(conn)


def _corpus_postcondition_counts(conn: sqlite3.Connection) -> dict:
    return {
        "sessions": int(conn.execute("SELECT count(*) FROM sessions").fetchone()[0]),
        "artifacts": int(conn.execute("SELECT count(*) FROM artifacts").fetchone()[0]),
        "messages": int(conn.execute("SELECT count(*) FROM messages").fetchone()[0]),
        "payload_pages": int(conn.execute("SELECT count(*) FROM payload_pages").fetchone()[0]),
        "message_sources": int(conn.execute("SELECT count(*) FROM message_sources").fetchone()[0]),
    }


def verify_ingest_postconditions(
    paths: CorpusPaths,
    sha256: str,
    *,
    check_sqlite_integrity: bool = True,
    include_corpus_counts: bool = True,
) -> dict:
    ledger = _read_one_accepted_entry(paths, sha256)
    if ledger is None:
        raise RuntimeError("RECONCILIATION_REQUIRED: accepted ledger missing")
    blob = _artifact_blob_path(paths, sha256)
    if not blob.exists() or blob.stat().st_size != int(ledger["size_bytes"]) or file_sha256(blob) != sha256:
        raise RuntimeError("RECONCILIATION_REQUIRED: artifact blob mismatch")
    conn = _connect_corpus(paths)
    try:
        row = conn.execute("SELECT * FROM artifacts WHERE sha256=?", (sha256,)).fetchone()
        if row is None:
            raise RuntimeError("RECONCILIATION_REQUIRED: projection artifact missing")
        if row["ledger_sha256"] != accepted_entry_digest(ledger):
            raise RuntimeError("RECONCILIATION_REQUIRED: ledger digest mismatch")
        integrity = _full_sqlite_integrity(conn) if check_sqlite_integrity else "DEFERRED_BATCH"
        if check_sqlite_integrity and integrity != "ok":
            raise RuntimeError("RECONCILIATION_REQUIRED: sqlite integrity failure")
        result = {"sqlite_integrity": integrity}
        if include_corpus_counts:
            result.update(_corpus_postcondition_counts(conn))
        else:
            result["corpus_counts"] = "DEFERRED_BATCH"
        return result
    finally:
        conn.close()


def write_ingest_receipt(
    paths: CorpusPaths,
    artifact_sha256: str,
    status: str,
    postconditions: dict,
) -> pathlib.Path:
    paths.ensure_layout()
    receipt = {
        "schema": "theseus.session-search-corpus-ingest-receipt.v1",
        "artifact_sha256": artifact_sha256,
        "status": status,
        "postconditions": postconditions,
        "recorded_at": _utc_now(),
    }
    target = paths.ingest_receipts / f"{artifact_sha256}-{uuid.uuid4().hex}.json"
    temp = paths.staging / f"receipt-{uuid.uuid4().hex}.tmp"
    try:
        data = _stable_json_bytes(receipt)
        with temp.open("xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, target)
        return target
    finally:
        if temp.exists():
            temp.unlink()


def _projection_routes_need_reconciliation(paths: CorpusPaths) -> bool:
    conn = _open_existing_projection(paths.db)
    if conn is None:
        return False
    try:
        try:
            missing = conn.execute(
                """
                SELECT count(*)
                FROM artifacts a
                LEFT JOIN artifact_routes r ON r.artifact_id=a.artifact_id
                WHERE r.artifact_id IS NULL
                   OR r.accepted_session_id<>a.session_id
                   OR r.route_version<>?
                   OR NOT EXISTS (
                       SELECT 1 FROM sessions s
                       WHERE s.session_id=r.projected_session_id
                   )
                   OR EXISTS (
                       SELECT 1 FROM payload_pages p
                       WHERE p.artifact_id=a.artifact_id
                         AND p.session_id<>r.projected_session_id
                   )
                """,
                (ROUTING_VERSION,),
            ).fetchone()[0]
        except sqlite3.Error:
            return True
        return int(missing) != 0
    finally:
        conn.close()


def _has_accepted_branched_chatgpt_family(paths: CorpusPaths, session_id: str) -> bool:
    conn = _open_existing_projection(paths.db)
    if conn is None:
        return False
    try:
        branch_ids = {
            str(row[0])
            for row in conn.execute(
                "SELECT session_id FROM artifacts WHERE source_adapter=? ORDER BY session_id",
                ("chatgpt-export",),
            ).fetchall()
            if str(row[0]) == session_id
            or str(row[0]).startswith(f"{session_id}~branch-")
        }
        return session_id in branch_ids and len(branch_ids) > 1
    finally:
        conn.close()


def ingest_reconciled_many(
    sources: Sequence[pathlib.Path],
    corpus_root: pathlib.Path,
) -> dict:
    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    source_paths = [pathlib.Path(source) for source in sources]
    with CorpusMutationLock(paths, "reconciled-ingest"):
        paths.ensure_layout()
        existing_entries = read_accepted_ledger(paths)
        conn = _open_existing_projection(paths.db)
        try:
            if conn is None:
                db_shas: set[str] = set()
            else:
                try:
                    db_shas = {
                        str(row[0])
                        for row in conn.execute("SELECT sha256 FROM artifacts").fetchall()
                    }
                except sqlite3.Error as exc:
                    raise RuntimeError(
                        f"RECONCILIATION_REQUIRED: projection artifact registry unreadable: {exc}"
                    ) from exc
            if set(existing_entries) != db_shas:
                raise RuntimeError(
                    "RECONCILIATION_REQUIRED: ledger/projection membership disagreement"
                )
        finally:
            if conn is not None:
                conn.close()

        combined_entries = dict(existing_entries)
        input_shas: list[str] = []
        new_shas: set[str] = set()
        seen_input_shas: set[str] = set()
        accepted_at = _utc_now()
        for source in source_paths:
            artifact = normalize_artifact(source)
            sha = artifact.artifact_sha256
            if sha in seen_input_shas:
                raise RuntimeError("FAILED_DUPLICATE_INGEST_SOURCE")
            seen_input_shas.add(sha)
            input_shas.append(sha)
            _copy_artifact_blob(paths, artifact)
            if sha in combined_entries:
                continue
            combined_entries[sha] = _accepted_entry_for_artifact(artifact, accepted_at)
            new_shas.add(sha)
            del artifact

        current_verify = _verify_projection(paths, paths.db)
        if not new_shas and current_verify.get("status") == "VERIFIED":
            results = []
            conn = _connect_corpus(paths)
            try:
                for sha in input_shas:
                    entry = combined_entries[sha]
                    artifact = _normalize_accepted_entry(paths, sha, entry)
                    db_row = conn.execute(
                        "SELECT * FROM artifacts WHERE sha256=?", (sha,)
                    ).fetchone()
                    if db_row is None:
                        raise RuntimeError(
                            "RECONCILIATION_REQUIRED: existing reconciled membership missing"
                        )
                    results.append(
                        _verify_existing_membership(
                            conn, paths, artifact, entry, db_row
                        )
                    )
            finally:
                conn.close()
            return {
                "status": "COMPLETE",
                "results": results,
                "batch_verification": current_verify,
            }

        candidate = paths.staging / f"reconciled-{uuid.uuid4().hex}.sqlite3"
        routes = _plan_projection_routes_for_entries(paths, combined_entries)
        _populate_projection_from_entries(
            paths, candidate, combined_entries, routes=routes
        )
        published_ledger_shas: list[str] = []
        try:
            candidate_verify = _verify_projection(
                paths, candidate, ledger_override=combined_entries
            )
            if candidate_verify.get("status") != "VERIFIED":
                raise RuntimeError(
                    f"RECONCILIATION_REQUIRED: candidate verification failed: {candidate_verify}"
                )
            for sha in sorted(new_shas):
                entry = combined_entries[sha]
                published_ledger_shas.append(sha)
                observed_path = write_accepted_entry(paths, entry)
                if json.loads(observed_path.read_text()) != entry:
                    raise RuntimeError("ACCEPTED_LEDGER_READBACK_MISMATCH")
            os.replace(candidate, paths.db)
        except Exception as exc:
            if candidate.exists():
                candidate.unlink()
            for sha in reversed(published_ledger_shas):
                path = accepted_entry_path(paths, sha)
                if path.exists():
                    path.unlink()
            raise RuntimeError(
                "RECONCILIATION_REQUIRED: reconciled ingest publication failed"
            ) from exc

        final = verify_corpus(paths.root)
        if final.get("status") != "VERIFIED":
            raise RuntimeError(
                f"RECONCILIATION_REQUIRED: reconciled ingest postcondition failed: {final}"
            )
        results = []
        for sha in input_shas:
            entry = combined_entries[sha]
            artifact = _normalize_accepted_entry(paths, sha, entry)
            route = routes[sha]
            status = "INGESTED" if sha in new_shas else "ALREADY_INGESTED"
            postconditions = verify_ingest_postconditions(paths, sha)
            write_ingest_receipt(paths, sha, status, postconditions)
            results.append(
                {
                    "status": status,
                    "mutation": "applied" if status == "INGESTED" else "none",
                    "artifact_sha256": sha,
                    "session_id": artifact.session_id,
                    "projected_session_id": route.projected_session_id,
                    "route_state": route.state,
                    "coverage_state": artifact.coverage_state,
                    **postconditions,
                }
            )
        return {"status": "COMPLETE", "results": results, "batch_verification": final}


class _RetryReconciledIngest(Exception):
    pass


def ingest_artifact(source: pathlib.Path, corpus_root: pathlib.Path, *, _defer_batch_verify: bool = False) -> dict:
    source = pathlib.Path(source)
    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    artifact = normalize_artifact(source)
    branch_source_id = (
        artifact.source_conversation_id
        if artifact.source_adapter == "chatgpt-export"
        else artifact.session_id
    )
    branch_sensitive = (
        artifact.source_adapter == "chatgpt-export"
        and artifact.branch_count is not None
        and artifact.branch_count > 1
    ) or (
        paths.accepted_ledger.exists()
        and branch_source_id is not None
        and _has_accepted_branched_chatgpt_family(paths, branch_source_id)
    )
    route_reconciliation_needed = (
        paths.db.exists() and _projection_routes_need_reconciliation(paths)
    )
    if branch_sensitive or route_reconciliation_needed:
        grouped = ingest_reconciled_many([source], paths.root)
        result = grouped["results"][0]
        if _defer_batch_verify:
            result = {**result, "sqlite_integrity": "DEFERRED_BATCH", "corpus_counts": "DEFERRED_BATCH"}
        return result
    try:
        with CorpusMutationLock(paths, "ingest"):
            locked_branch_sensitive = (
                artifact.source_adapter == "chatgpt-export"
                and artifact.branch_count is not None
                and artifact.branch_count > 1
            ) or (
                paths.accepted_ledger.exists()
                and branch_source_id is not None
                and _has_accepted_branched_chatgpt_family(paths, branch_source_id)
            )
            locked_route_reconciliation_needed = (
                paths.db.exists() and _projection_routes_need_reconciliation(paths)
            )
            if locked_branch_sensitive or locked_route_reconciliation_needed:
                raise _RetryReconciledIngest
            if artifact.source_adapter == "speed-booster-export":
                from .speed_booster_export import validate_materialized_artifact_identity

                validate_materialized_artifact_identity(source, paths.root)
            _copy_artifact_blob(paths, artifact)
            conn = _connect_corpus(paths)
            ledger_written = False
            try:
                ledger, db_row = _membership_state(conn, paths, artifact.artifact_sha256)
                if ledger is not None or db_row is not None:
                    if ledger is None or db_row is None:
                        raise RuntimeError("RECONCILIATION_REQUIRED: ledger/projection membership disagreement")
                    return _verify_existing_membership(conn, paths, artifact, ledger, db_row, check_sqlite_integrity=not _defer_batch_verify)

                accepted_at = _utc_now()
                entry = _accepted_entry_for_artifact(artifact, accepted_at)
                conn.execute("BEGIN IMMEDIATE")
                delta = apply_normalized_artifact_to_projection(conn, paths, artifact, accepted_at)
                assert_transaction_invariants(conn, artifact, check_global=not _defer_batch_verify)
                ledger_path = write_accepted_entry(paths, entry)
                observed = json.loads(ledger_path.read_text())
                if observed != entry:
                    raise RuntimeError("ACCEPTED_LEDGER_READBACK_MISMATCH")
                ledger_written = True
                conn.commit()
            except Exception as exc:
                try:
                    conn.rollback()
                finally:
                    conn.close()
                if ledger_written:
                    raise RuntimeError(
                        "RECONCILIATION_REQUIRED: ledger accepted but projection commit failed"
                    ) from exc
                raise
            else:
                conn.close()

            postconditions = verify_ingest_postconditions(
                paths,
                artifact.artifact_sha256,
                check_sqlite_integrity=not _defer_batch_verify,
                include_corpus_counts=not _defer_batch_verify,
            )
            write_ingest_receipt(
                paths,
                artifact_sha256=artifact.artifact_sha256,
                status="INGESTED",
                postconditions=postconditions,
            )
            return {
                "status": "INGESTED",
                "mutation": "applied",
                "artifact_sha256": artifact.artifact_sha256,
                "session_id": artifact.session_id,
                "coverage_state": artifact.coverage_state,
                **delta,
                **postconditions,
            }
    except _RetryReconciledIngest:
        grouped = ingest_reconciled_many([source], paths.root)
        result = grouped["results"][0]
        if _defer_batch_verify:
            result = {**result, "sqlite_integrity": "DEFERRED_BATCH", "corpus_counts": "DEFERRED_BATCH"}
        return result


def _chatgpt_branch_batch_key(
    source: pathlib.Path,
) -> tuple[str, str] | None:
    try:
        with zipfile.ZipFile(source) as zf:
            manifest = json.loads(zf.read("manifest.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    if not isinstance(manifest, dict):
        return None
    if manifest.get("source_adapter") != "chatgpt-export":
        return None
    branch_count = manifest.get("branch_count")
    source_export_sha256 = manifest.get("source_export_sha256")
    source_conversation_id = manifest.get("source_conversation_id")
    if (
        isinstance(branch_count, bool)
        or not isinstance(branch_count, int)
        or branch_count <= 1
        or not isinstance(source_export_sha256, str)
        or not source_export_sha256
        or not isinstance(source_conversation_id, str)
        or not source_conversation_id
    ):
        return None
    return source_export_sha256, source_conversation_id


def ingest_many(
    sources: Sequence[pathlib.Path],
    corpus_root: pathlib.Path,
) -> dict:
    source_paths = [pathlib.Path(source) for source in sources]

    if any(_chatgpt_branch_batch_key(source) is not None for source in source_paths):
        try:
            batch = ingest_reconciled_many(source_paths, corpus_root)
            batch_results = batch.get("results")
            if not isinstance(batch_results, list) or len(batch_results) != len(source_paths):
                raise RuntimeError(
                    "RECONCILIATION_REQUIRED: batch result cardinality mismatch"
                )
            return {
                **batch,
                "results": [
                    {"source": str(source), **row}
                    for source, row in zip(source_paths, batch_results, strict=True)
                ],
            }
        except Exception as exc:
            results = [
                {"source": str(source), "status": "FAILED", "error": str(exc)}
                for source in source_paths
            ]
            batch_verification = verify_corpus(pathlib.Path(corpus_root))
            return {
                "status": "DEGRADED",
                "results": results,
                "batch_verification": batch_verification,
            }

    results = []
    for source in source_paths:
        try:
            results.append(
                {
                    "source": str(source),
                    **ingest_artifact(
                        source, corpus_root, _defer_batch_verify=True
                    ),
                }
            )
        except Exception as exc:
            results.append(
                {"source": str(source), "status": "FAILED", "error": str(exc)}
            )

    batch_verification = verify_corpus(pathlib.Path(corpus_root))
    complete = (
        all(r.get("status") != "FAILED" for r in results)
        and batch_verification.get("status") == "VERIFIED"
    )
    return {
        "status": "COMPLETE" if complete else "DEGRADED",
        "results": results,
        "batch_verification": batch_verification,
    }


def corpus_status(corpus_root: pathlib.Path) -> dict:
    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    paths.ensure_layout()
    ledger = read_accepted_ledger(paths)
    accepted_entries = list(ledger.values())
    source_adapters: dict[str, int] = {}
    for entry in accepted_entries:
        adapter = str(entry.get("source_adapter") or "unknown")
        source_adapters[adapter] = source_adapters.get(adapter, 0) + 1
    accepted_times = [str(entry["accepted_at"]) for entry in accepted_entries if entry.get("accepted_at")]
    base = {
        "status": "OBSERVED",
        "currentness": "UNKNOWN_WITHOUT_SOURCE_WATERMARK",
        "accepted_artifacts": len(accepted_entries),
        "source_adapters": dict(sorted(source_adapters.items())),
        "latest_accepted_at": max(accepted_times) if accepted_times else None,
        "latest_observed_message_time": None,
        "coverage_states": {},
    }

    if not paths.db.exists() and not accepted_entries:
        return {
            **base,
            "projection_status": "UNINITIALIZED",
            "sessions": 0,
            "messages": 0,
        }

    verification = verify_corpus(paths.root)
    base["projection_status"] = str(verification.get("status") or "UNKNOWN")
    if verification.get("status") != "VERIFIED":
        return {
            **base,
            "sessions": None,
            "messages": None,
        }

    conn = _open_existing_projection(paths.db)
    if conn is None:
        return {
            **base,
            "projection_status": "RECONCILIATION_REQUIRED",
            "sessions": None,
            "messages": None,
        }
    try:
        coverage_states = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                "SELECT coverage_state,count(*) FROM sessions GROUP BY coverage_state ORDER BY coverage_state"
            ).fetchall()
        }
        latest_observed = conn.execute(
            "SELECT max(observed_max_time) FROM artifacts WHERE observed_max_time IS NOT NULL"
        ).fetchone()[0]
        return {
            **base,
            "projection_status": "VERIFIED",
            "sessions": int(verification.get("sessions", 0)),
            "messages": int(verification.get("messages", 0)),
            "coverage_states": coverage_states,
            "latest_observed_message_time": None if latest_observed is None else float(latest_observed),
        }
    finally:
        conn.close()


def read_lock_status(paths: CorpusPaths) -> dict:
    if not paths.mutation_lock.exists():
        return {"locked": False}
    owner_path = paths.mutation_lock / "owner.json"
    if not owner_path.exists():
        return {"locked": True, "owner_state": "MISSING"}
    try:
        owner = json.loads(owner_path.read_text())
    except Exception as exc:
        return {"locked": True, "owner_state": "INVALID", "error": str(exc)}
    return {"locked": True, "owner_state": "PRESENT", **owner}


def _open_existing_projection(db_path: pathlib.Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _verify_projection(
    paths: CorpusPaths,
    db_path: pathlib.Path,
    *,
    ledger_override: Mapping[str, dict] | None = None,
) -> dict:
    ledger = (
        read_accepted_ledger(paths)
        if ledger_override is None
        else dict(ledger_override)
    )
    for sha, entry in ledger.items():
        blob = _artifact_blob_path(paths, sha)
        if not blob.exists():
            return {"status": "FAILED_INTEGRITY", "reason": "accepted artifact missing", "artifact_sha256": sha}
        if blob.stat().st_size != int(entry["size_bytes"]) or file_sha256(blob) != sha:
            return {"status": "FAILED_INTEGRITY", "reason": "accepted artifact hash/size mismatch", "artifact_sha256": sha}

    conn = _open_existing_projection(db_path)
    if conn is None:
        if ledger:
            return {"status": "RECONCILIATION_REQUIRED", "reason": "projection missing"}
        return {"status": "RECONCILIATION_REQUIRED", "reason": "corpus projection not initialized"}
    try:
        try:
            schema_row = conn.execute("SELECT value FROM corpus_meta WHERE key='schema_version'").fetchone()
        except sqlite3.Error as exc:
            return {"status": "RECONCILIATION_REQUIRED", "reason": f"projection schema unreadable: {exc}"}
        if schema_row is None or schema_row[0] != CORPUS_SCHEMA_VERSION:
            return {"status": "RECONCILIATION_REQUIRED", "reason": "projection schema mismatch"}
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            return {"status": "FAILED_INTEGRITY", "reason": "sqlite integrity failure", "sqlite_integrity": integrity}
        db_rows = conn.execute("SELECT * FROM artifacts ORDER BY sha256").fetchall()
        db_map = {str(row["sha256"]): row for row in db_rows}
        if set(db_map) != set(ledger):
            return {
                "status": "RECONCILIATION_REQUIRED",
                "reason": "accepted ledger/projection membership disagreement",
                "ledger_members": len(ledger),
                "projection_members": len(db_map),
            }
        try:
            route_rows = conn.execute(
                """
                SELECT a.sha256,a.session_id AS artifact_session_id,
                       r.accepted_session_id,r.projected_session_id,r.route_state,r.route_reason,r.route_version
                FROM artifacts a
                LEFT JOIN artifact_routes r ON r.artifact_id=a.artifact_id
                ORDER BY a.sha256
                """
            ).fetchall()
        except sqlite3.Error:
            return {"status": "RECONCILIATION_REQUIRED", "reason": "artifact routing projection missing"}
        if len(route_rows) != len(db_rows):
            return {"status": "RECONCILIATION_REQUIRED", "reason": "artifact routing membership disagreement"}
        for route_row in route_rows:
            if route_row["accepted_session_id"] is None:
                return {"status": "RECONCILIATION_REQUIRED", "reason": "artifact route missing", "artifact_sha256": route_row["sha256"]}
            if route_row["accepted_session_id"] != route_row["artifact_session_id"]:
                return {"status": "RECONCILIATION_REQUIRED", "reason": "artifact route source identity mismatch", "artifact_sha256": route_row["sha256"]}
            if route_row["route_version"] != ROUTING_VERSION:
                return {"status": "RECONCILIATION_REQUIRED", "reason": "artifact route version mismatch", "artifact_sha256": route_row["sha256"]}
            if route_row["route_state"] not in {"DIRECT", "BRANCH_MATCH", "UNRESOLVED"}:
                return {"status": "RECONCILIATION_REQUIRED", "reason": "artifact route state invalid", "artifact_sha256": route_row["sha256"]}
            session_exists = conn.execute(
                "SELECT count(*) FROM sessions WHERE session_id=?",
                (route_row["projected_session_id"],),
            ).fetchone()[0]
            if int(session_exists) != 1:
                return {"status": "RECONCILIATION_REQUIRED", "reason": "artifact projected session missing", "artifact_sha256": route_row["sha256"]}
            page_mismatch = conn.execute(
                """
                SELECT count(*) FROM payload_pages p
                JOIN artifacts a ON a.artifact_id=p.artifact_id
                WHERE a.sha256=? AND p.session_id<>?
                """,
                (route_row["sha256"], route_row["projected_session_id"]),
            ).fetchone()[0]
            if int(page_mismatch):
                return {"status": "RECONCILIATION_REQUIRED", "reason": "artifact page route mismatch", "artifact_sha256": route_row["sha256"]}
        for sha, entry in ledger.items():
            row = db_map[sha]
            if row["ledger_sha256"] != accepted_entry_digest(entry):
                return {"status": "RECONCILIATION_REQUIRED", "reason": "ledger digest mismatch", "artifact_sha256": sha}
            if str(row["session_id"]) != str(entry["session_id"]):
                return {"status": "RECONCILIATION_REQUIRED", "reason": "session identity mismatch", "artifact_sha256": sha}
            if int(row["size_bytes"]) != int(entry["size_bytes"]):
                return {"status": "RECONCILIATION_REQUIRED", "reason": "artifact size metadata mismatch", "artifact_sha256": sha}
            if str(row["accepted_at"]) != str(entry["accepted_at"]):
                return {"status": "RECONCILIATION_REQUIRED", "reason": "accepted timestamp mismatch", "artifact_sha256": sha}
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            return {"status": "FAILED_INTEGRITY", "reason": "foreign key check failed", "rows": len(fk)}
        dup = conn.execute(
            """
            SELECT count(*) FROM (
                SELECT session_id,message_id,count(*) AS n
                FROM messages WHERE message_id IS NOT NULL
                GROUP BY session_id,message_id HAVING n>1
            )
            """
        ).fetchone()[0]
        if int(dup) != 0:
            return {"status": "FAILED_INTEGRITY", "reason": "identified message uniqueness failure"}
        searchable = int(
            conn.execute(
                "SELECT count(*) FROM messages WHERE text<>'' AND search_class<>'hidden'"
            ).fetchone()[0]
        )
        fts_rows = int(conn.execute("SELECT count(*) FROM messages_fts").fetchone()[0])
        if searchable != fts_rows:
            return {
                "status": "FAILED_INTEGRITY",
                "reason": "fts row coverage mismatch",
                "searchable_messages": searchable,
                "fts_rows": fts_rows,
            }
        for session in conn.execute("SELECT * FROM sessions ORDER BY session_id").fetchall():
            expected = _session_metadata_values(conn, str(session["session_id"]))
            for key, value in expected.items():
                if session[key] != value:
                    return {
                        "status": "RECONCILIATION_REQUIRED",
                        "reason": f"session metadata mismatch: {key}",
                        "session_id": str(session["session_id"]),
                    }
        return {
            "status": "VERIFIED",
            "sqlite_integrity": integrity,
            "sessions": int(conn.execute("SELECT count(*) FROM sessions").fetchone()[0]),
            "artifacts": len(db_rows),
            "messages": int(conn.execute("SELECT count(*) FROM messages").fetchone()[0]),
            "payload_pages": int(conn.execute("SELECT count(*) FROM payload_pages").fetchone()[0]),
            "message_sources": int(conn.execute("SELECT count(*) FROM message_sources").fetchone()[0]),
            "fts_rows": fts_rows,
        }
    except sqlite3.Error as exc:
        return {
            "status": "RECONCILIATION_REQUIRED",
            "reason": f"projection unreadable: {exc}",
        }
    finally:
        conn.close()


def _normalize_accepted_entry(
    paths: CorpusPaths,
    sha: str,
    entry: dict,
) -> NormalizedArtifact:
    blob = _artifact_blob_path(paths, sha)
    if (
        not blob.exists()
        or blob.stat().st_size != int(entry["size_bytes"])
        or file_sha256(blob) != sha
    ):
        raise RuntimeError(f"FAILED_INTEGRITY: accepted artifact {sha} invalid")
    artifact = normalize_artifact(blob)
    if artifact.artifact_sha256 != sha:
        raise RuntimeError("FAILED_INTEGRITY: normalized artifact hash mismatch")
    if (
        artifact.session_id != entry["session_id"]
        or artifact.coverage_state != entry["coverage_state"]
    ):
        raise RuntimeError("RECONCILIATION_REQUIRED: accepted ledger metadata mismatch")
    return artifact


def _iter_normalized_entries(
    paths: CorpusPaths,
    entries: dict[str, dict],
    *,
    adapter: str | None = None,
):
    for sha, entry in sorted(entries.items()):
        if adapter is not None and entry.get("source_adapter") != adapter:
            continue
        yield entry, _normalize_accepted_entry(paths, sha, entry)


def _plan_projection_routes_for_entries(
    paths: CorpusPaths,
    entries: dict[str, dict],
) -> dict[str, ProjectionRoute]:
    families = build_official_families(
        artifact
        for _entry, artifact in _iter_normalized_entries(
            paths, entries, adapter="chatgpt-export"
        )
    )
    routes: dict[str, ProjectionRoute] = {}
    for sha, entry in sorted(entries.items()):
        artifact = _normalize_accepted_entry(paths, sha, entry)
        routes[sha] = route_artifact(artifact, families)
    return routes


def _populate_projection_from_entries(
    paths: CorpusPaths,
    target: pathlib.Path,
    entries: dict[str, dict],
    routes: dict[str, ProjectionRoute] | None = None,
) -> dict[str, ProjectionRoute]:
    if target.exists():
        target.unlink()
    routes = routes or _plan_projection_routes_for_entries(paths, entries)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    try:
        init_corpus_db(conn)
        conn.execute("BEGIN")
        for sha, entry in sorted(entries.items()):
            artifact = _normalize_accepted_entry(paths, sha, entry)
            apply_normalized_artifact_to_projection(
                conn, paths, artifact, str(entry["accepted_at"]), routes[sha]
            )
            assert_transaction_invariants(conn, artifact, check_global=False)
        for row in conn.execute("SELECT session_id FROM sessions ORDER BY session_id").fetchall():
            _recompute_ordinals(conn, str(row["session_id"]))
            _recompute_session_metadata(conn, str(row["session_id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        if target.exists():
            target.unlink()
        raise
    else:
        conn.close()
    return routes


def _build_projection_from_accepted_artifacts(
    paths: CorpusPaths,
    target: pathlib.Path,
) -> dict:
    entries = read_accepted_ledger(paths)
    _populate_projection_from_entries(paths, target, entries)
    return _verify_projection(paths, target)


_SEMANTIC_STREAM_QUERIES = (
    (
        "sessions",
        """
        SELECT session_id,title,coverage_state,coverage_reason,first_message_time,last_message_time,
               first_accepted_at,last_accepted_at,title_source_time,title_source_artifact_sha256
        FROM sessions ORDER BY session_id
        """,
    ),
    (
        "artifacts",
        """
        SELECT sha256,size_bytes,source_schema,source_adapter,observed_title,accepted_at,
               coverage_state,session_id,ledger_sha256,observed_min_time,observed_max_time,
               observed_message_count
        FROM artifacts ORDER BY sha256
        """,
    ),
    (
        "routes",
        """
        SELECT a.sha256,r.accepted_session_id,r.projected_session_id,r.route_state,r.route_reason,r.route_version
        FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id
        ORDER BY a.sha256
        """,
    ),
    (
        "pages",
        """
        SELECT a.sha256,p.session_id,p.capture_sequence,p.member_name,p.start_cursor,p.end_cursor,
               p.has_previous_page,p.has_next_page,p.message_count,p.min_create_time,p.max_create_time
        FROM payload_pages p JOIN artifacts a ON a.artifact_id=p.artifact_id
        ORDER BY a.sha256,p.capture_sequence,p.member_name
        """,
    ),
    (
        "messages",
        """
        SELECT session_id,ordinal,message_id,local_identity,canonical_message_sha256,
               role,content_type,search_class,create_time,provider_order,text
        FROM messages ORDER BY session_id,ordinal,local_identity
        """,
    ),
    (
        "sources",
        """
        SELECT m.session_id,m.local_identity,a.sha256,p.member_name,ms.page_position,
               ms.source_message_id,ms.source_object_sha256
        FROM message_sources ms
        JOIN messages m ON m.row_id=ms.message_row_id
        JOIN payload_pages p ON p.page_id=ms.page_id
        JOIN artifacts a ON a.artifact_id=p.artifact_id
        ORDER BY m.session_id,m.local_identity,a.sha256,p.member_name,ms.page_position
        """,
    ),
    (
        "fts_vocab",
        """
        SELECT m.session_id,m.local_identity,v.term,v.col,v.offset
        FROM temp.messages_fts_vocab v
        JOIN messages m ON m.row_id=v.doc
        ORDER BY m.session_id,m.local_identity,v.term,v.col,v.offset
        """,
    ),
    (
        "fts_config",
        "SELECT k, v FROM messages_fts_config ORDER BY k",
    ),
    (
        "fts_docsize",
        """
        SELECT m.session_id,m.local_identity,hex(d.sz)
        FROM messages_fts_docsize d
        JOIN messages m ON m.row_id=d.id
        ORDER BY m.session_id,m.local_identity
        """,
    ),
)


def _semantic_projection_equal(left_db: pathlib.Path, right_db: pathlib.Path) -> tuple[bool, str | None]:
    left = _open_existing_projection(pathlib.Path(left_db))
    right = _open_existing_projection(pathlib.Path(right_db))
    if left is None or right is None:
        if left is not None:
            left.close()
        if right is not None:
            right.close()
        return False, "projection_missing"
    try:
        for conn in (left, right):
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS temp.messages_fts_vocab "
                "USING fts5vocab(main, messages_fts, 'instance')"
            )
        for label, sql in _SEMANTIC_STREAM_QUERIES:
            left_cursor = left.execute(sql)
            right_cursor = right.execute(sql)
            row_index = 0
            while True:
                left_row = left_cursor.fetchone()
                right_row = right_cursor.fetchone()
                if left_row is None or right_row is None:
                    if left_row is None and right_row is None:
                        break
                    return False, f"{label}:length:{row_index}"
                if tuple(left_row) != tuple(right_row):
                    return False, f"{label}:row:{row_index}"
                row_index += 1

        scalar_queries = (
            ("fts_rows", "SELECT count(*) FROM messages_fts"),
            ("fts_global_stats", "SELECT id,hex(block) FROM messages_fts_data WHERE id=1"),
            ("fts_definition", "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages_fts'"),
        )
        for label, sql in scalar_queries:
            left_row = left.execute(sql).fetchone()
            right_row = right.execute(sql).fetchone()
            left_value = None if left_row is None else tuple(left_row)
            right_value = None if right_row is None else tuple(right_row)
            if left_value != right_value:
                return False, label
        return True, None
    except sqlite3.Error as exc:
        return False, f"sqlite:{exc}"
    finally:
        left.close()
        right.close()


def verify_corpus(corpus_root: pathlib.Path) -> dict:
    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    paths.ensure_layout()
    current = _verify_projection(paths, paths.db)
    if current.get("status") != "VERIFIED":
        return current

    verify_root = pathlib.Path(tempfile.mkdtemp(prefix="session-search-verify-"))
    candidate = verify_root / "derived.sqlite3"
    try:
        derived = _build_projection_from_accepted_artifacts(paths, candidate)
        if derived.get("status") != "VERIFIED":
            return {
                "status": "RECONCILIATION_REQUIRED",
                "reason": "artifact-derived verification projection failed",
                "derived": derived,
            }
        equivalent, component = _semantic_projection_equal(paths.db, candidate)
        if not equivalent:
            return {
                "status": "RECONCILIATION_REQUIRED",
                "reason": "current projection does not derive from accepted artifacts",
                "component": component,
            }
        return current
    finally:
        shutil.rmtree(verify_root, ignore_errors=True)


def semantic_snapshot(corpus_root: pathlib.Path, db_path: pathlib.Path | None = None) -> dict:
    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    target = paths.db if db_path is None else pathlib.Path(db_path)
    conn = _open_existing_projection(target)
    if conn is None:
        raise RuntimeError("projection missing")
    try:
        sessions = [tuple(row) for row in conn.execute(
            """
            SELECT session_id,title,coverage_state,coverage_reason,first_message_time,last_message_time,
                   first_accepted_at,last_accepted_at,title_source_time,title_source_artifact_sha256
            FROM sessions ORDER BY session_id
            """
        ).fetchall()]
        artifacts = [tuple(row) for row in conn.execute(
            """
            SELECT sha256,size_bytes,source_schema,source_adapter,observed_title,accepted_at,
                   coverage_state,session_id,ledger_sha256,observed_min_time,observed_max_time,
                   observed_message_count
            FROM artifacts ORDER BY sha256
            """
        ).fetchall()]
        routes = [tuple(row) for row in conn.execute(
            """
            SELECT a.sha256,r.accepted_session_id,r.projected_session_id,r.route_state,r.route_reason,r.route_version
            FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id
            ORDER BY a.sha256
            """
        ).fetchall()]
        pages = [tuple(row) for row in conn.execute(
            """
            SELECT a.sha256,p.session_id,p.capture_sequence,p.member_name,p.start_cursor,p.end_cursor,
                   p.has_previous_page,p.has_next_page,p.message_count,p.min_create_time,p.max_create_time
            FROM payload_pages p JOIN artifacts a ON a.artifact_id=p.artifact_id
            ORDER BY a.sha256,p.capture_sequence,p.member_name
            """
        ).fetchall()]
        messages = [tuple(row) for row in conn.execute(
            """
            SELECT session_id,ordinal,message_id,local_identity,canonical_message_sha256,
                   role,content_type,search_class,create_time,provider_order,text
            FROM messages ORDER BY session_id,ordinal,local_identity
            """
        ).fetchall()]
        sources = [tuple(row) for row in conn.execute(
            """
            SELECT m.session_id,m.local_identity,a.sha256,p.member_name,ms.page_position,
                   ms.source_message_id,ms.source_object_sha256
            FROM message_sources ms
            JOIN messages m ON m.row_id=ms.message_row_id
            JOIN payload_pages p ON p.page_id=ms.page_id
            JOIN artifacts a ON a.artifact_id=p.artifact_id
            ORDER BY m.session_id,m.local_identity,a.sha256,p.member_name,ms.page_position
            """
        ).fetchall()]
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.messages_fts_vocab "
            "USING fts5vocab(main, messages_fts, 'instance')"
        )
        fts_vocab = [
            tuple(row)
            for row in conn.execute(
                """
                SELECT m.session_id,m.local_identity,v.term,v.col,v.offset
                FROM temp.messages_fts_vocab v
                JOIN messages m ON m.row_id=v.doc
                ORDER BY m.session_id,m.local_identity,v.term,v.col,v.offset
                """
            ).fetchall()
        ]
        fts_config = [
            tuple(row)
            for row in conn.execute(
                "SELECT k, v FROM messages_fts_config ORDER BY k"
            ).fetchall()
        ]
        fts_docsize = [
            tuple(row)
            for row in conn.execute(
                """
                SELECT m.session_id,m.local_identity,hex(d.sz)
                FROM messages_fts_docsize d
                JOIN messages m ON m.row_id=d.id
                ORDER BY m.session_id,m.local_identity
                """
            ).fetchall()
        ]
        fts_global_stats_row = conn.execute(
            "SELECT id,hex(block) FROM messages_fts_data WHERE id=1"
        ).fetchone()
        fts_global_stats = None if fts_global_stats_row is None else tuple(fts_global_stats_row)
        fts_definition_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        ).fetchone()
        fts_definition = None if fts_definition_row is None else str(fts_definition_row[0])
        return {
            "sessions": sessions,
            "artifacts": artifacts,
            "routes": routes,
            "pages": pages,
            "messages": messages,
            "sources": sources,
            "fts_rows": int(conn.execute("SELECT count(*) FROM messages_fts").fetchone()[0]),
            "fts_vocab": fts_vocab,
            "fts_config": fts_config,
            "fts_docsize": fts_docsize,
            "fts_global_stats": fts_global_stats,
            "fts_definition": fts_definition,
        }
    finally:
        conn.close()


def _write_rebuild_receipt(paths: CorpusPaths, result: dict) -> pathlib.Path:
    paths.ensure_layout()
    receipt = {
        "schema": "theseus.session-search-corpus-rebuild-receipt.v1",
        "recorded_at": _utc_now(),
        **result,
    }
    target = paths.rebuild_receipts / f"rebuild-{uuid.uuid4().hex}.json"
    temp = paths.staging / f"rebuild-receipt-{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("xb") as fh:
            fh.write(_stable_json_bytes(receipt))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, target)
        return target
    finally:
        if temp.exists():
            temp.unlink()


def rebuild_corpus(corpus_root: pathlib.Path) -> dict:
    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    with CorpusMutationLock(paths, "rebuild"):
        old_verify = verify_corpus(paths.root)
        compare_old_projection = old_verify.get("status") == "VERIFIED"

        new_db = paths.root / "corpus.sqlite3.new"
        new_verify = _build_projection_from_accepted_artifacts(paths, new_db)
        if new_verify.get("status") != "VERIFIED":
            raise RuntimeError(f"REBUILD_VERIFY_FAILED: {new_verify}")
        if compare_old_projection:
            equivalent, component = _semantic_projection_equal(paths.db, new_db)
            if not equivalent:
                raise RuntimeError(f"REBUILD_EQUIVALENCE_MISMATCH: {component}")
        try:
            os.replace(new_db, paths.db)
        except OSError:
            result = {"status": "REBUILD_SWAP_BLOCKED", **new_verify}
            _write_rebuild_receipt(paths, result)
            return result
        final_verify = verify_corpus(paths.root)
        if final_verify.get("status") != "VERIFIED":
            raise RuntimeError(f"REBUILD_POSTCONDITION_FAILED: {final_verify}")
        result = {"status": "REBUILT", **{k: v for k, v in final_verify.items() if k != "status"}}
        _write_rebuild_receipt(paths, result)
        return result
