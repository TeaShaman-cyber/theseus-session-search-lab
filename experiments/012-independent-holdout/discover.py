#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3


DIRECT_MARKERS = (
    'вспомни',
    'восстанови',
    'по нашим разговорам',
    'session search',
    'recall',
    'recover',
    'find in our conversations',
)
HISTORY_MARKERS = (
    'разговор',
    'истори',
    'чат',
    'предыдущ',
    'раньше',
    'старый',
    'старую',
    'session',
    'сессион',
)


def is_historical_retrieval_candidate(text: str) -> bool:
    value = text.casefold()
    if any(marker in value for marker in DIRECT_MARKERS):
        return True
    if 'найди' in value and any(marker in value for marker in HISTORY_MARKERS):
        return True
    return False


def public_summary(rows: list[dict]) -> dict:
    times = [row['create_time'] for row in rows if row['create_time'] is not None]
    return {
        'schema': 'theseus.session-search-holdout-discovery.v1',
        'candidate_count': len(rows),
        'candidate_digest': canonical_digest(rows),
        'first_create_time': min(times) if times else None,
        'last_create_time': max(times) if times else None,
    }


def discover(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''
        SELECT session_id,ordinal,message_id,create_time,text
        FROM messages
        WHERE role='user'
          AND search_class='dialogue'
          AND text IS NOT NULL
        ORDER BY create_time,session_id,ordinal
        '''
    ).fetchall()
    return [dict(row) for row in rows if is_historical_retrieval_candidate(row['text'])]


def canonical_digest(rows: list[dict]) -> str:
    payload = json.dumps(
        [
            {
                'session_id': row['session_id'],
                'ordinal': row['ordinal'],
                'message_id': row['message_id'],
                'create_time': row['create_time'],
                'text': row['text'],
            }
            for row in rows
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--corpus', type=pathlib.Path, required=True)
    parser.add_argument('--private-out', type=pathlib.Path, required=True)
    parser.add_argument('--public-out', type=pathlib.Path)
    args = parser.parse_args()

    db = args.corpus / 'corpus.sqlite3' if args.corpus.is_dir() else args.corpus
    conn = sqlite3.connect(f'file:{db.resolve()}?mode=ro&immutable=1', uri=True)
    try:
        rows = discover(conn)
    finally:
        conn.close()

    args.private_out.parent.mkdir(parents=True, exist_ok=True)
    args.private_out.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8',
    )
    args.private_out.chmod(0o600)

    public = public_summary(rows)
    if args.public_out:
        args.public_out.write_text(
            json.dumps(public, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
    print(json.dumps({
        'candidate_count': len(rows),
        'candidate_digest': public['candidate_digest'],
        'private_mode': oct(args.private_out.stat().st_mode & 0o777),
    }, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
