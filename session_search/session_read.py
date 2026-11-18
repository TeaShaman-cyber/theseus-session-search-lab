"""Read an accepted conversation in bounded ordered portions, without lexical search."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import sqlite3
from urllib.parse import quote

from .corpus_store import CorpusPaths, resolve_corpus_root


def _projection_stamp(path: pathlib.Path) -> str:
    """Best-effort local revision guard, not a cryptographic corpus verification."""
    parts = []
    for candidate in (path, pathlib.Path(str(path) + "-wal")):
        try:
            s = candidate.stat()
            parts.append([s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns])
        except FileNotFoundError:
            parts.append(None)
    return hashlib.sha256(json.dumps(parts).encode("ascii")).hexdigest()


def _cursor_encode(session_id: str, revision: str, ordinal: int, offset: int) -> str:
    data = {"v": 1, "s": session_id, "rev": revision, "o": ordinal, "offset": offset}
    return base64.urlsafe_b64encode(
        json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii").rstrip("=")


def _cursor_decode(token: str) -> dict:
    if not isinstance(token, str) or len(token) > 4096 or not token:
        raise ValueError("CURSOR_INVALID")
    try:
        data = json.loads(base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True))
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        raise ValueError("CURSOR_INVALID") from exc
    if (
        not isinstance(data, dict) or set(data) != {"v", "s", "rev", "o", "offset"}
        or type(data["v"]) is not int or data["v"] != 1
        or not isinstance(data["s"], str) or not isinstance(data["rev"], str)
        or type(data["o"]) is not int or data["o"] < 0
        or type(data["offset"]) is not int or data["offset"] < 0
    ):
        raise ValueError("CURSOR_INVALID")
    return data


def read_session(
    corpus_root: pathlib.Path,
    session_id: str,
    *,
    limit: int = 20,
    max_chars: int = 16000,
    start_ordinal: int = 0,
    cursor: str | None = None,
) -> dict:
    """Return a bounded next page of message segments in original session order."""
    if not isinstance(session_id, str) or not session_id or len(session_id) > 512:
        raise ValueError("SESSION_OUT_OF_BOUNDS")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("LIMIT_OUT_OF_BOUNDS")
    if type(max_chars) is not int or not 1 <= max_chars <= 100000:
        raise ValueError("CHAR_BUDGET_OUT_OF_BOUNDS")
    if type(start_ordinal) is not int or start_ordinal < 0:
        raise ValueError("ORDINAL_OUT_OF_BOUNDS")
    if cursor is not None and start_ordinal != 0:
        raise ValueError("CURSOR_AND_START_CONFLICT")

    db = CorpusPaths.from_root(corpus_root).db
    if not db.is_file():
        raise ValueError("PROJECTION_UNAVAILABLE")
    decoded = _cursor_decode(cursor) if cursor is not None else None
    if decoded is not None and decoded["s"] != session_id:
        raise ValueError("CURSOR_SESSION_MISMATCH")

    connection = sqlite3.connect(f"file:{quote(str(db.resolve()))}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        revision = _projection_stamp(db)
        if decoded is not None and decoded["rev"] != revision:
            raise ValueError("STALE_CURSOR")
        session = connection.execute(
            "SELECT title, coverage_state, coverage_reason FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if session is None:
            raise ValueError("SESSION_NOT_FOUND")

        ordinal = decoded["o"] if decoded is not None else start_ordinal
        offset = decoded["offset"] if decoded is not None else 0
        rows = connection.execute(
            """SELECT ordinal, message_id, role, content_type, search_class, create_time, text
               FROM messages WHERE session_id=? AND ordinal>=?
               ORDER BY ordinal, row_id LIMIT ?""",
            (session_id, ordinal, limit),
        ).fetchall()
        messages: list[dict] = []
        remaining = max_chars
        for row in rows:
            full_text = row["text"]
            start = offset if row["ordinal"] == ordinal else 0
            if start >= len(full_text) and start != 0:
                raise ValueError("CURSOR_INVALID")
            segment = full_text[start:start + remaining]
            complete = start + len(segment) >= len(full_text)
            messages.append({
                "ordinal": row["ordinal"],
                "message_id": row["message_id"],
                "role": row["role"],
                "content_type": row["content_type"],
                "search_class": row["search_class"],
                "create_time": row["create_time"],
                "text": segment,
                "text_offset": start,
                "text_total_chars": len(full_text),
                "complete_message": complete,
            })
            remaining -= len(segment)
            ordinal = row["ordinal"] + (1 if complete else 0)
            offset = 0 if complete else start + len(segment)
            if not complete or remaining == 0:
                break

        has_more = connection.execute(
            "SELECT 1 FROM messages WHERE session_id=? AND ordinal>=? LIMIT 1",
            (session_id, ordinal),
        ).fetchone() is not None
        if _projection_stamp(db) != revision:
            raise ValueError("PROJECTION_CHANGED_DURING_READ")
        return {
            "session_id": session_id,
            "session_title": session["title"],
            "session_coverage": session["coverage_state"],
            "coverage_reason": session["coverage_reason"],
            "projection_stamp": revision,
            "messages": messages,
            "has_more": has_more,
            "next_cursor": _cursor_encode(session_id, revision, ordinal, offset) if has_more else None,
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read one accepted Session Search conversation.")
    parser.add_argument("session_id")
    parser.add_argument("--corpus")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-chars", type=int, default=16000)
    parser.add_argument("--start-ordinal", type=int, default=0)
    parser.add_argument("--cursor")
    args = parser.parse_args(argv)
    try:
        corpus_root = resolve_corpus_root(args.corpus)
        result = read_session(
            corpus_root, args.session_id,
            limit=args.limit, max_chars=args.max_chars,
            start_ordinal=args.start_ordinal, cursor=args.cursor,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
