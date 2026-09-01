from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

from .artifact import normalize_artifact

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


def import_export(source: pathlib.Path, db_path: pathlib.Path) -> dict:
    if db_path.exists():
        db_path.unlink()
    artifact = normalize_artifact(source)
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
            (artifact.session_id, artifact.title, artifact.coverage_state, int(artifact.has_previous_page), int(artifact.has_next_page), artifact.source_schema, artifact.source_adapter),
        )
        page_ids = {}
        for page in artifact.pages:
            cur = conn.execute(
                "INSERT INTO payload_pages(session_id,capture_sequence,member_name,start_cursor,end_cursor,has_previous_page,has_next_page,message_count,min_create_time,max_create_time) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (artifact.session_id, page.capture_sequence, page.member_name, page.start_cursor, page.end_cursor, int(page.has_previous_page), int(page.has_next_page), page.message_count, page.min_create_time, page.max_create_time),
            )
            page_ids[page.capture_sequence] = int(cur.lastrowid)

        indexed = 0
        class_counts = {}
        for ordinal, message in enumerate(artifact.messages):
            class_counts[message.search_class] = class_counts.get(message.search_class, 0) + 1
            message_id = message.message_id or ""
            cur = conn.execute(
                "INSERT INTO messages(session_id,ordinal,message_id,role,content_type,search_class,create_time,text) VALUES (?,?,?,?,?,?,?,?)",
                (artifact.session_id, ordinal, message_id, message.role, message.content_type, message.search_class, message.create_time, message.text),
            )
            row_id = int(cur.lastrowid)
            for source_ref in message.sources:
                conn.execute(
                    "INSERT INTO message_sources(message_row_id,message_id,page_id,page_position) VALUES (?,?,?,?)",
                    (row_id, message_id, page_ids[source_ref.capture_sequence], source_ref.page_position),
                )
            if message.text and message.search_class != "hidden":
                conn.execute(
                    "INSERT INTO messages_fts(rowid,text,role,content_type,search_class,message_id,ordinal) VALUES (?,?,?,?,?,?,?)",
                    (row_id, message.text, message.role, message.content_type, message.search_class, message_id, ordinal),
                )
                indexed += 1
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return {
        "coverage_state": artifact.coverage_state,
        "has_previous_page": artifact.has_previous_page,
        "has_next_page": artifact.has_next_page,
        "messages": len(artifact.messages),
        "message_occurrences": artifact.message_occurrences,
        "duplicate_message_occurrences": artifact.duplicate_message_occurrences,
        "payload_pages": len(artifact.pages),
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
