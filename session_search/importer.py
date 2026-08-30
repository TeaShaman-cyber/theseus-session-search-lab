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
PAYLOAD_RE = re.compile(r"^optional/conversation(?:-messages)?-[^/]+\.bin$")


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
        CREATE TABLE payload_pages (
            page_id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            capture_sequence INTEGER NOT NULL,
            member_name TEXT NOT NULL UNIQUE,
            start_cursor TEXT,
            end_cursor TEXT,
            has_previous_page INTEGER NOT NULL,
            has_next_page INTEGER NOT NULL,
            message_count INTEGER NOT NULL,
            min_create_time REAL,
            max_create_time REAL
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
        CREATE TABLE message_sources (
            message_row_id INTEGER NOT NULL,
            message_id TEXT,
            page_id INTEGER NOT NULL,
            page_position INTEGER NOT NULL,
            PRIMARY KEY (message_row_id, page_id, page_position)
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


def _message_signature(message: dict) -> str:
    return json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _page_time_bounds(messages: list[dict]) -> tuple[float | None, float | None]:
    values = [float(m["create_time"]) for m in messages if isinstance(m.get("create_time"), (int, float))]
    return (min(values), max(values)) if values else (None, None)


def _load_payload_pages(zf: zipfile.ZipFile, manifest: dict) -> list[dict]:
    manifest_names = [item.get("name") for item in manifest.get("files", [])]
    payload_names = [name for name in manifest_names if isinstance(name, str) and PAYLOAD_RE.match(name)]
    if not payload_names:
        raise ValueError("no conversation payloads found")
    pages = []
    for capture_sequence, name in enumerate(payload_names):
        obj = json.loads(zf.read(name).decode("utf-8"))
        messages = obj.get("messages") or []
        if not isinstance(messages, list):
            raise ValueError(f"messages must be a list: {name}")
        page_info = obj.get("page_info") or {}
        min_time, max_time = _page_time_bounds(messages)
        pages.append({
            "capture_sequence": capture_sequence,
            "member_name": name,
            "object": obj,
            "messages": messages,
            "page_info": page_info,
            "min_create_time": min_time,
            "max_create_time": max_time,
        })
    return pages


def _chronology_key(page: dict) -> tuple[int, float, int]:
    min_time = page["min_create_time"]
    if min_time is None:
        return (1, 0.0, -int(page["capture_sequence"]))
    return (0, float(min_time), int(page["capture_sequence"]))


def import_export(source: pathlib.Path, db_path: pathlib.Path) -> dict:
    if db_path.exists():
        db_path.unlink()
    with zipfile.ZipFile(source) as zf:
        safe_members(zf)
        manifest = json.loads(zf.read("manifest.json"))
        verify_manifest(zf, manifest)
        pages = _load_payload_pages(zf, manifest)

    detail = next((p["object"] for p in pages if p["object"].get("conversation_id")), {})
    session_id = str(detail.get("conversation_id") or "session")
    title = str(detail.get("title") or "")
    chronological_pages = sorted(pages, key=_chronology_key)
    oldest_page = chronological_pages[0]
    newest_page = chronological_pages[-1]
    oldest_has_previous = bool((oldest_page["page_info"] or {}).get("has_previous_page"))
    newest_has_next = bool((newest_page["page_info"] or {}).get("has_next_page"))
    coverage = "COMPLETE_EXPOSED_CONVERSATION" if not oldest_has_previous else "PARTIAL_SESSION_SLICE"

    canonical = {}
    signatures = {}
    sources = {}
    anonymous_counter = 0
    duplicate_occurrences = 0
    for page in pages:
        for position, msg in enumerate(page["messages"]):
            raw_id = str(msg.get("id") or "")
            if raw_id:
                key = f"id:{raw_id}"
                signature = _message_signature(msg)
                if key in canonical:
                    duplicate_occurrences += 1
                    if signatures[key] != signature:
                        raise ValueError(f"conflicting duplicate message_id: {raw_id}")
                else:
                    canonical[key] = msg
                    signatures[key] = signature
            else:
                key = f"anon:{anonymous_counter}"
                anonymous_counter += 1
                canonical[key] = msg
                signatures[key] = _message_signature(msg)
            sources.setdefault(key, []).append((int(page["capture_sequence"]), position))

    def message_order(item):
        key, msg = item
        create_time = msg.get("create_time")
        first_source = min(sources[key])
        if isinstance(create_time, (int, float)):
            return (0, float(create_time), first_source[0], first_source[1])
        return (1, 0.0, first_source[0], first_source[1])

    ordered_messages = sorted(canonical.items(), key=message_order)
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
            (session_id, title, coverage, int(oldest_has_previous), int(newest_has_next), manifest.get("schema"), "barn-doctor"),
        )
        page_ids = {}
        for page in pages:
            info = page["page_info"] or {}
            cur = conn.execute(
                "INSERT INTO payload_pages(session_id,capture_sequence,member_name,start_cursor,end_cursor,has_previous_page,has_next_page,message_count,min_create_time,max_create_time) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (session_id, page["capture_sequence"], page["member_name"], info.get("start_cursor"), info.get("end_cursor"), int(bool(info.get("has_previous_page"))), int(bool(info.get("has_next_page"))), len(page["messages"]), page["min_create_time"], page["max_create_time"]),
            )
            page_ids[int(page["capture_sequence"])] = int(cur.lastrowid)

        indexed = 0
        class_counts = {}
        for ordinal, (key, msg) in enumerate(ordered_messages):
            author = msg.get("author") or {}
            role = str(author.get("role") or "unknown")
            content = msg.get("content") or {}
            content_type = str(content.get("content_type") or "unknown")
            search_class = classify(role, content_type)
            text = extract_text(content)
            class_counts[search_class] = class_counts.get(search_class, 0) + 1
            message_id = str(msg.get("id") or "")
            cur = conn.execute(
                "INSERT INTO messages(session_id,ordinal,message_id,role,content_type,search_class,create_time,text) VALUES (?,?,?,?,?,?,?,?)",
                (session_id, ordinal, message_id, role, content_type, search_class, msg.get("create_time"), text),
            )
            row_id = int(cur.lastrowid)
            for capture_sequence, page_position in sources[key]:
                conn.execute(
                    "INSERT INTO message_sources(message_row_id,message_id,page_id,page_position) VALUES (?,?,?,?)",
                    (row_id, message_id, page_ids[capture_sequence], page_position),
                )
            if text and search_class != "hidden":
                conn.execute(
                    "INSERT INTO messages_fts(rowid,text,role,content_type,search_class,message_id,ordinal) VALUES (?,?,?,?,?,?,?)",
                    (row_id, text, role, content_type, search_class, message_id, ordinal),
                )
                indexed += 1
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return {
        "coverage_state": coverage,
        "has_previous_page": oldest_has_previous,
        "has_next_page": newest_has_next,
        "messages": len(ordered_messages),
        "message_occurrences": sum(len(p["messages"]) for p in pages),
        "duplicate_message_occurrences": duplicate_occurrences,
        "payload_pages": len(pages),
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
