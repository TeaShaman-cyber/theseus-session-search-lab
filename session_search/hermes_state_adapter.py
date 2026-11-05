"""Capture adapter: Hermes state.db -> portable session artifact.

Contract: docs/capture-adapter-contract.md.

What this preserves, and what it refuses to claim:

- session identity  = state.db ``session_id``;
- message identity  = ``messages.id`` (stable within the database);
- ordering          = ``timestamp``, then ``id`` as a tiebreak;
- coverage          = PARTIAL. The database holds what was written to it; it is
  not proof that an earlier message never existed. Deleted or compacted rows,
  sessions from other profiles, and messages that predate this database are all
  outside the artifact and outside the claim.
- integrity         = sha256 of the payload, in both the manifest and file list.

Artifact layout, matching what the importer already normalises:

    manifest.json                                    schema + file hashes
    optional/conversation-<session>-<hash>.bin       one page of messages

Message shape is the generic one the normaliser expects::

    {"author": {"role": "user|assistant|tool|system"},
     "content": {"content_type": "text", "parts": ["..."]},
     "create_time": 1793391465.0}

Usage::

    python3 -m session_search.hermes_state_adapter \
        --db "$LOCALAPPDATA/hermes/state.db" \
        --out /tmp/hermes-state \
        --session <session_id>          # repeatable; omit for every session
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import zipfile

SCHEMA = "theseus.session-search.hermes-state.v1"
ADAPTER = "hermes-state"
COVERAGE = "PARTIAL_DATABASE_SLICE"

# Rows the retrieval projection treats as noise are still carried: dropping them
# here would make the adapter decide what is evidence, which is not its job.
ROLE_MAP = {"user": "user", "assistant": "assistant", "tool": "tool", "system": "system"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _connect(db: pathlib.Path) -> sqlite3.Connection:
    if not db.is_file():
        raise SystemExit(f"database not found: {db}")
    # Read-only: the adapter must never be able to mutate session history.
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _sessions(conn: sqlite3.Connection, only: list[str]) -> list[sqlite3.Row]:
    # sessions.id, not session_id: the column is named ``id`` while messages join
    # on ``session_id``. Guessing here produced "no such column".
    if only:
        placeholders = ",".join("?" for _ in only)
        return conn.execute(
            f"SELECT id, title FROM sessions WHERE id IN ({placeholders}) ORDER BY id",
            only,
        ).fetchall()
    return conn.execute("SELECT id, title FROM sessions ORDER BY id").fetchall()


def _messages(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, role, content, timestamp
        FROM messages
        WHERE session_id = ?
        ORDER BY timestamp ASC, id ASC
        """,
        (session_id,),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        text = row["content"]
        if not isinstance(text, str) or not text.strip():
            continue
        role = ROLE_MAP.get(str(row["role"]), "unknown")
        out.append(
            {
                "author": {"role": role},
                "content": {"content_type": "text", "parts": [text]},
                "create_time": float(row["timestamp"]) if row["timestamp"] is not None else None,
                "metadata": {"hermes_message_id": row["id"]},
            }
        )
    return out


def build_page(session_id: str, title: str | None, messages: list[dict]) -> dict:
    times = [m["create_time"] for m in messages if isinstance(m.get("create_time"), (int, float))]
    return {
        "conversation_id": session_id,
        "title": title or session_id,
        "messages": messages,
        "page_info": {"has_previous_page": False, "has_next_page": False},
        "min_create_time": min(times) if times else None,
        "max_create_time": max(times) if times else None,
        "coverage": COVERAGE,
    }


def write_artifact(session_id: str, page: dict, out_dir: pathlib.Path) -> pathlib.Path:
    payload = json.dumps(page, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload_sha = _sha256(payload)
    member = f"optional/conversation-{session_id}-{payload_sha[:16]}.bin"

    manifest = {
        "schema": SCHEMA,
        "source_adapter": ADAPTER,
        "session_id": session_id,
        "source_conversation_id": session_id,
        "snapshot_scope": COVERAGE,
        "branch_count": 1,
        "files": [{"name": member, "bytes": len(payload), "sha256": payload_sha}],
    }
    body = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")

    target = out_dir / f"{session_id}-{payload_sha[:16]}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, payload)
        z.writestr("manifest.json", body)
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    ap.add_argument("--session", action="append", default=[], help="session id; repeatable")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    conn = _connect(args.db)
    sessions = _sessions(conn, args.session)

    written = 0
    skipped = 0
    total_messages = 0
    for row in sessions:
        session_id = row["id"]
        messages = _messages(conn, session_id)
        if not messages:
            skipped += 1
            continue
        page = build_page(session_id, row["title"], messages)
        write_artifact(session_id, page, args.out)
        written += 1
        total_messages += len(messages)

    print(f"sessions={len(sessions)} written={written} skipped_empty={skipped} messages={total_messages}")
    print(f"out={args.out} coverage={COVERAGE} (coverage states what this database held, not what happened)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
