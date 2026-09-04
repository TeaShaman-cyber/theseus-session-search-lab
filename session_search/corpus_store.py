from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import shutil
import socket
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from .artifact import NormalizedArtifact, file_sha256, normalize_artifact

CORPUS_SCHEMA_VERSION = "session-search-corpus-v1"
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
        INSERT INTO corpus_meta(key,value) VALUES ('schema_version','session-search-corpus-v1');

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


def _verify_existing_membership(
    conn: sqlite3.Connection,
    paths: CorpusPaths,
    artifact: NormalizedArtifact,
    ledger: dict,
    db_row: sqlite3.Row,
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
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("RECONCILIATION_REQUIRED: sqlite integrity failure")
    return {
        "status": "ALREADY_INGESTED",
        "mutation": "none",
        "artifact_sha256": artifact.artifact_sha256,
        "session_id": artifact.session_id,
    }


def _ensure_session_stub(conn: sqlite3.Connection, artifact: NormalizedArtifact, accepted_at: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO sessions(
            session_id,title,coverage_state,coverage_reason,
            first_message_time,last_message_time,first_accepted_at,last_accepted_at,
            title_source_time,title_source_artifact_sha256
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            artifact.session_id,
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


def _insert_pages(conn: sqlite3.Connection, artifact: NormalizedArtifact, artifact_id: int) -> dict[int, int]:
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
                artifact.session_id,
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


def _upsert_messages(
    conn: sqlite3.Connection,
    artifact: NormalizedArtifact,
    page_ids: dict[int, int],
) -> dict:
    novel = 0
    reused = 0
    source_additions = 0
    for message in artifact.messages:
        row = None
        if message.message_id is not None:
            row = conn.execute(
                "SELECT * FROM messages WHERE session_id=? AND message_id=?",
                (artifact.session_id, message.message_id),
            ).fetchone()
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
                    artifact.session_id,
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
            if row["canonical_message_sha256"] != message.canonical_message_sha256:
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
        "SELECT * FROM artifacts WHERE session_id=?",
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
    artifact: NormalizedArtifact,
    accepted_at: str,
) -> dict:
    _ensure_session_stub(conn, artifact, accepted_at)
    artifact_id = _insert_artifact_row(conn, artifact, accepted_at)
    page_ids = _insert_pages(conn, artifact, artifact_id)
    delta = _upsert_messages(conn, artifact, page_ids)
    _recompute_ordinals(conn, artifact.session_id)
    _recompute_session_metadata(conn, artifact.session_id)
    return {"artifact_id": artifact_id, **delta}


def assert_transaction_invariants(conn: sqlite3.Connection, artifact: NormalizedArtifact) -> None:
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("FAILED_TRANSACTION: foreign key invariant")
    row = conn.execute("SELECT count(*) FROM artifacts WHERE sha256=?", (artifact.artifact_sha256,)).fetchone()
    if int(row[0]) != 1:
        raise RuntimeError("FAILED_TRANSACTION: artifact registry invariant")
    duplicates = conn.execute(
        """
        SELECT session_id,message_id,count(*)
        FROM messages WHERE message_id IS NOT NULL
        GROUP BY session_id,message_id HAVING count(*)>1
        """
    ).fetchall()
    if duplicates:
        raise RuntimeError("FAILED_TRANSACTION: identified message uniqueness invariant")


def verify_ingest_postconditions(paths: CorpusPaths, sha256: str) -> dict:
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
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError("RECONCILIATION_REQUIRED: sqlite integrity failure")
        return {
            "sqlite_integrity": integrity,
            "sessions": int(conn.execute("SELECT count(*) FROM sessions").fetchone()[0]),
            "artifacts": int(conn.execute("SELECT count(*) FROM artifacts").fetchone()[0]),
            "messages": int(conn.execute("SELECT count(*) FROM messages").fetchone()[0]),
            "payload_pages": int(conn.execute("SELECT count(*) FROM payload_pages").fetchone()[0]),
            "message_sources": int(conn.execute("SELECT count(*) FROM message_sources").fetchone()[0]),
        }
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


def ingest_artifact(source: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    source = pathlib.Path(source)
    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    with CorpusMutationLock(paths, "ingest"):
        artifact = normalize_artifact(source)
        _copy_artifact_blob(paths, artifact)
        conn = _connect_corpus(paths)
        ledger_written = False
        try:
            ledger, db_row = _membership_state(conn, paths, artifact.artifact_sha256)
            if ledger is not None or db_row is not None:
                if ledger is None or db_row is None:
                    raise RuntimeError("RECONCILIATION_REQUIRED: ledger/projection membership disagreement")
                return _verify_existing_membership(conn, paths, artifact, ledger, db_row)

            accepted_at = _utc_now()
            entry = _accepted_entry_for_artifact(artifact, accepted_at)
            conn.execute("BEGIN IMMEDIATE")
            delta = apply_normalized_artifact_to_projection(conn, artifact, accepted_at)
            assert_transaction_invariants(conn, artifact)
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

        postconditions = verify_ingest_postconditions(paths, artifact.artifact_sha256)
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


def ingest_many(
    sources: Sequence[pathlib.Path],
    corpus_root: pathlib.Path,
) -> dict:
    results = []
    for source in sources:
        try:
            results.append({"source": str(source), **ingest_artifact(pathlib.Path(source), corpus_root)})
        except Exception as exc:
            results.append({"source": str(source), "status": "FAILED", "error": str(exc)})
    return {
        "status": "COMPLETE" if all(r.get("status") != "FAILED" for r in results) else "DEGRADED",
        "results": results,
    }



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


def _verify_projection(paths: CorpusPaths, db_path: pathlib.Path) -> dict:
    ledger = read_accepted_ledger(paths)
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
    finally:
        conn.close()


def verify_corpus(corpus_root: pathlib.Path) -> dict:
    paths = CorpusPaths.from_root(pathlib.Path(corpus_root))
    paths.ensure_layout()
    return _verify_projection(paths, paths.db)


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
                   role,content_type,search_class,create_time,text
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
        return {
            "sessions": sessions,
            "artifacts": artifacts,
            "pages": pages,
            "messages": messages,
            "sources": sources,
            "fts_rows": int(conn.execute("SELECT count(*) FROM messages_fts").fetchone()[0]),
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
        ledger = read_accepted_ledger(paths)
        normalized: list[tuple[dict, NormalizedArtifact]] = []
        for sha, entry in sorted(ledger.items(), key=lambda item: item[0]):
            blob = _artifact_blob_path(paths, sha)
            if not blob.exists() or blob.stat().st_size != int(entry["size_bytes"]) or file_sha256(blob) != sha:
                raise RuntimeError(f"FAILED_INTEGRITY: accepted artifact {sha} invalid")
            artifact = normalize_artifact(blob)
            if artifact.artifact_sha256 != sha:
                raise RuntimeError("FAILED_INTEGRITY: normalized artifact hash mismatch")
            if artifact.session_id != entry["session_id"] or artifact.coverage_state != entry["coverage_state"]:
                raise RuntimeError("RECONCILIATION_REQUIRED: accepted ledger metadata mismatch")
            normalized.append((entry, artifact))

        old_verify = _verify_projection(paths, paths.db)
        old_snapshot = semantic_snapshot(paths.root) if old_verify.get("status") == "VERIFIED" else None

        new_db = paths.root / "corpus.sqlite3.new"
        if new_db.exists():
            new_db.unlink()
        conn = sqlite3.connect(new_db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            init_corpus_db(conn)
            conn.execute("BEGIN")
            for entry, artifact in normalized:
                apply_normalized_artifact_to_projection(conn, artifact, str(entry["accepted_at"]))
                assert_transaction_invariants(conn, artifact)
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            if new_db.exists():
                new_db.unlink()
            raise
        else:
            conn.close()

        new_verify = _verify_projection(paths, new_db)
        if new_verify.get("status") != "VERIFIED":
            raise RuntimeError(f"REBUILD_VERIFY_FAILED: {new_verify}")
        new_snapshot = semantic_snapshot(paths.root, new_db)
        if old_snapshot is not None and new_snapshot != old_snapshot:
            raise RuntimeError("REBUILD_EQUIVALENCE_MISMATCH")
        try:
            os.replace(new_db, paths.db)
        except OSError:
            result = {"status": "REBUILD_SWAP_BLOCKED", **new_verify}
            _write_rebuild_receipt(paths, result)
            return result
        final_verify = _verify_projection(paths, paths.db)
        if final_verify.get("status") != "VERIFIED":
            raise RuntimeError(f"REBUILD_POSTCONDITION_FAILED: {final_verify}")
        result = {"status": "REBUILT", **{k: v for k, v in final_verify.items() if k != "status"}}
        _write_rebuild_receipt(paths, result)
        return result
