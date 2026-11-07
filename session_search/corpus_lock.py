from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import tempfile

from session_search.corpus_store import CORPUS_SCHEMA_VERSION

LOCK_SCHEMA = "theseus.session-search-corpus-lock.v1"


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_version(db_path: pathlib.Path) -> str:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro&immutable=1", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM corpus_meta WHERE key='schema_version'"
        ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("CORPUS_SCHEMA_UNREADABLE") from exc
    finally:
        conn.close()
    if row is None:
        raise ValueError("CORPUS_SCHEMA_MISSING")
    return str(row[0])


def _validated_db(db_path: pathlib.Path) -> pathlib.Path:
    db = pathlib.Path(db_path).expanduser().resolve()
    if not db.is_file():
        raise ValueError("CORPUS_DB_MISSING")
    schema_version = _schema_version(db)
    if schema_version != CORPUS_SCHEMA_VERSION:
        raise ValueError(
            f"CORPUS_SCHEMA_MISMATCH expected={CORPUS_SCHEMA_VERSION} actual={schema_version}"
        )
    return db


def pin_corpus(db_path: pathlib.Path, lock_path: pathlib.Path) -> dict:
    db = _validated_db(db_path)
    payload = {
        "schema": LOCK_SCHEMA,
        "db_path": str(db),
        "db_sha256": _sha256(db),
        "size_bytes": db.stat().st_size,
        "sqlite_schema_version": CORPUS_SCHEMA_VERSION,
    }

    lock = pathlib.Path(lock_path).expanduser()
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{lock.name}.",
        suffix=".tmp",
        dir=lock.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, lock)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return payload


def resolve_corpus_lock(lock_path: pathlib.Path) -> pathlib.Path:
    lock = pathlib.Path(lock_path).expanduser()
    if not lock.is_file():
        raise ValueError("CORPUS_LOCK_MISSING")
    try:
        payload = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("CORPUS_LOCK_INVALID") from exc
    if payload.get("schema") != LOCK_SCHEMA:
        raise ValueError("CORPUS_LOCK_SCHEMA_MISMATCH")

    required = ("db_path", "db_sha256", "size_bytes", "sqlite_schema_version")
    if any(key not in payload for key in required):
        raise ValueError("CORPUS_LOCK_INCOMPLETE")
    if payload["sqlite_schema_version"] != CORPUS_SCHEMA_VERSION:
        raise ValueError("CORPUS_LOCK_SQLITE_SCHEMA_MISMATCH")

    db = _validated_db(pathlib.Path(payload["db_path"]))
    if db.stat().st_size != int(payload["size_bytes"]):
        raise ValueError("CORPUS_LOCK_SIZE_MISMATCH")
    if _sha256(db) != str(payload["db_sha256"]):
        raise ValueError("CORPUS_LOCK_DIGEST_MISMATCH")
    return db


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pin or verify the private SQLite corpus selected for decision-bearing experiments."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pin = sub.add_parser("pin")
    pin.add_argument("--db", type=pathlib.Path, required=True)
    pin.add_argument("--lock", type=pathlib.Path, required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--lock", type=pathlib.Path, required=True)

    args = parser.parse_args()
    if args.command == "pin":
        payload = pin_corpus(args.db, args.lock)
        print(
            json.dumps(
                {
                    "status": "PINNED",
                    "size_bytes": payload["size_bytes"],
                    "sqlite_schema_version": payload["sqlite_schema_version"],
                },
                sort_keys=True,
            )
        )
        return 0

    db = resolve_corpus_lock(args.lock)
    print(json.dumps({"status": "VERIFIED", "db_path": str(db)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
