from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import socket
import sqlite3
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone

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
            canonical_message_sha256 TEXT NOT NULL,
            role TEXT NOT NULL,
            content_type TEXT NOT NULL,
            search_class TEXT NOT NULL,
            create_time REAL,
            text TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions(session_id)
        );
        CREATE UNIQUE INDEX messages_identified_identity
        ON messages(session_id, message_id)
        WHERE message_id IS NOT NULL;
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
