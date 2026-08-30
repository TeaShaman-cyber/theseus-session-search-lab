from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sqlite3
import sys
import zipfile

HIDDEN_TYPES = {"thoughts", "reasoning_recap", "model_editable_context"}
PAYLOAD_RE = re.compile(r"^optional/conversation-[^/]+\.bin$")


def safe_members(zf: zipfile.ZipFile) -> list[str]:
    names = zf.namelist()
    for name in names:
        p = pathlib.PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"unsafe zip member: {name}")
    return names


def verify_manifest(zf: zipfile.ZipFile, manifest: dict) -> None:
    available = set(zf.namelist())
    for item in manifest.get("files", []):
        name = item.get("name")
        if not isinstance(name, str) or name not in available:
            raise ValueError(f"manifest member missing: {name}")
        data = zf.read(name)
        expected_size = item.get("bytes")
        expected_sha = item.get("sha256")
        if expected_size is not None and len(data) != int(expected_size):
            raise ValueError(f"size mismatch: {name}")
        if expected_sha and hashlib.sha256(data).hexdigest() != expected_sha:
            raise ValueError(f"sha256 mismatch: {name}")


def extract_text(content: dict) -> str:
    ctype = str(content.get("content_type") or "")
    if ctype == "text":
        parts = content.get("parts") or []
        return "\n".join(str(x) for x in parts if isinstance(x, (str, int, float)))
    if ctype == "code":
        return str(content.get("text") or "")
    if ctype == "tether_browsing_display":
        return str(content.get("text") or content.get("result") or "")
    return ""


def classify(role: str, content_type: str) -> str:
    if role == "system" or content_type in HIDDEN_TYPES:
        return "hidden"
    if role == "tool":
        return "evidence"
    if role in {"user", "assistant"} and content_type == "text":
        return "dialogue"
    return "trace"


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            title TEXT,
            coverage_state TEXT NOT NULL,
            has_previous_page INTEGER NOT NULL,
            has_next_page INTEGER NOT NULL,
            source_schema TEXT,
            source_adapter TEXT
        );
        CREATE TABLE messages (
            row_id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            message_id TEXT,
            role TEXT NOT NULL,
            content_type TEXT NOT NULL,
            search_class TEXT NOT NULL,
            create_time REAL,
            text TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            text,
            role UNINDEXED,
            content_type UNINDEXED,
            search_class UNINDEXED,
            message_id UNINDEXED,
            ordinal UNINDEXED,
            content=''
        );
        """
    )


def import_export(source: pathlib.Path, db_path: pathlib.Path) -> dict:
    if db_path.exists():
        db_path.unlink()
    with zipfile.ZipFile(source) as zf:
        safe_members(zf)
        manifest = json.loads(zf.read("manifest.json"))
        verify_manifest(zf, manifest)
        payloads = [n for n in zf.namelist() if PAYLOAD_RE.match(n)]
        if len(payloads) != 1:
            raise ValueError(f"expected exactly one conversation payload, found {len(payloads)}")
        raw = zf.read(payloads[0])
        conversation = json.loads(raw.decode("utf-8"))

    page = conversation.get("page_info") or {}
    has_previous = bool(page.get("has_previous_page"))
    has_next = bool(page.get("has_next_page"))
    coverage = "PARTIAL_SESSION_SLICE" if has_previous or has_next else "CAPTURED_SLICE_COMPLETE"
    session_id = str(conversation.get("conversation_id") or "session")
    title = str(conversation.get("title") or "")

    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
            (session_id, title, coverage, int(has_previous), int(has_next), manifest.get("schema"), "barn-doctor"),
        )
        indexed = 0
        class_counts: dict[str, int] = {}
        for ordinal, msg in enumerate(conversation.get("messages") or []):
            author = msg.get("author") or {}
            role = str(author.get("role") or "unknown")
            content = msg.get("content") or {}
            content_type = str(content.get("content_type") or "unknown")
            search_class = classify(role, content_type)
            text = extract_text(content)
            class_counts[search_class] = class_counts.get(search_class, 0) + 1
            cur = conn.execute(
                "INSERT INTO messages(session_id,ordinal,message_id,role,content_type,search_class,create_time,text) VALUES (?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    ordinal,
                    str(msg.get("id") or ""),
                    role,
                    content_type,
                    search_class,
                    msg.get("create_time"),
                    text,
                ),
            )
            if text and search_class != "hidden":
                conn.execute(
                    "INSERT INTO messages_fts(rowid,text,role,content_type,search_class,message_id,ordinal) VALUES (?,?,?,?,?,?,?)",
                    (cur.lastrowid, text, role, content_type, search_class, str(msg.get("id") or ""), ordinal),
                )
                indexed += 1
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return {
        "coverage_state": coverage,
        "has_previous_page": has_previous,
        "has_next_page": has_next,
        "messages": len(conversation.get("messages") or []),
        "indexed": indexed,
        "search_classes": class_counts,
        "integrity": integrity,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Import a portable captured session artifact into a regeneratable SQLite FTS5 projection.")
    p.add_argument("source", type=pathlib.Path)
    p.add_argument("--db", required=True, type=pathlib.Path)
    args = p.parse_args(argv)
    try:
        result = import_export(args.source, args.db)
    except Exception as exc:
        print(f"IMPORT FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
