from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

NEON_API_BASE = 'https://console.neon.tech/api/v2'
LOADER_ROLE = 'session_search_arcade_loader_sql'
_PROJECT_ID_RE = re.compile(r'^[a-z0-9-]{1,60}$')
_BRANCH_ID_RE = re.compile(r'^br-[a-z0-9-]{1,60}$')
_DATABASE_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}$')


class TargetMaterializationError(ValueError):
    pass


def _validate_target(project_id: str, branch_id: str, database_name: str) -> None:
    if not _PROJECT_ID_RE.fullmatch(project_id or ''):
        raise TargetMaterializationError('NEON_PROJECT_ID_INVALID')
    if not _BRANCH_ID_RE.fullmatch(branch_id or ''):
        raise TargetMaterializationError('NEON_BRANCH_ID_INVALID')
    if not _DATABASE_RE.fullmatch(database_name or ''):
        raise TargetMaterializationError('NEON_DATABASE_NAME_INVALID')


async def resolve_neon_connection_uri(
    *,
    neon_api_key: str,
    project_id: str,
    branch_id: str,
    database_name: str = 'neondb',
    client: Any | None = None,
) -> str:
    if not neon_api_key:
        raise TargetMaterializationError('NEON_API_KEY_MISSING')
    _validate_target(project_id, branch_id, database_name)
    own_client = client is None
    if own_client:
        import httpx
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
    try:
        response = await client.get(
            f"{NEON_API_BASE}/projects/{quote(project_id, safe='')}/connection_uri",
            headers={'Authorization': f'Bearer {neon_api_key}'},
            params={
                'branch_id': branch_id,
                'database_name': database_name,
                'role_name': LOADER_ROLE,
                'pooled': 'false',
            },
        )
        response.raise_for_status()
        payload = response.json()
        uri = payload.get('uri') if isinstance(payload, dict) else None
        if not isinstance(uri, str) or not uri.startswith(('postgresql://', 'postgres://')):
            raise TargetMaterializationError('NEON_CONNECTION_URI_INVALID')
        return uri
    finally:
        if own_client:
            await client.aclose()


def classify_existing_generation(row: tuple[Any, ...] | None, expected: dict[str, Any]) -> str | None:
    if row is None:
        return None
    if len(row) != 7:
        raise TargetMaterializationError('TARGET_EXISTING_GENERATION_SHAPE_INVALID')
    shard_id, provider, source_manifest, projection_schema, state, sessions, messages = row
    identity_matches = (
        shard_id == expected['shard_id']
        and provider == expected['provider']
        and source_manifest == expected['source_manifest_sha256']
        and projection_schema == expected['projection_schema_version']
        and int(sessions) == int(expected['session_count'])
        and int(messages) == int(expected['message_count'])
    )
    if not identity_matches:
        raise TargetMaterializationError('TARGET_EXISTING_GENERATION_MISMATCH')
    if state == 'BUILDING':
        return 'ALREADY_BUILDING'
    if state == 'READY':
        return 'ALREADY_READY'
    raise TargetMaterializationError('TARGET_EXISTING_GENERATION_STATE_INVALID')


def load_rows_to_building(
    *,
    manifest: dict[str, Any],
    sessions: list[tuple[Any, ...]],
    messages: list[tuple[Any, ...]],
    database_url: str,
    batch_size: int = 500,
    connect: Any | None = None,
    execute_values_fn: Any | None = None,
) -> dict[str, Any]:
    if not database_url:
        raise TargetMaterializationError('NEON_CONNECTION_URI_MISSING')
    if batch_size < 1 or batch_size > 5000:
        raise TargetMaterializationError('BATCH_SIZE_OUT_OF_BOUNDS')
    required = (
        'generation_id', 'shard_id', 'provider', 'source_manifest_sha256',
        'projection_schema_version', 'session_count', 'message_count',
    )
    if any(key not in manifest for key in required):
        raise TargetMaterializationError('BUNDLE_MANIFEST_INCOMPLETE_FOR_TARGET')

    if connect is None or execute_values_fn is None:
        import psycopg2
        from psycopg2.extras import execute_values
        if connect is None:
            connect = psycopg2.connect
        if execute_values_fn is None:
            execute_values_fn = execute_values

    conn = connect(database_url)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT g.shard_id,g.provider,g.source_manifest_sha256,g.projection_schema_version,g.state,"
                    "(SELECT count(*) FROM session_search.sessions s WHERE s.generation_id=g.generation_id),"
                    "(SELECT count(*) FROM session_search.messages m WHERE m.generation_id=g.generation_id) "
                    "FROM session_search.generations g WHERE g.generation_id=%s",
                    (manifest['generation_id'],),
                )
                existing = cur.fetchone()
                replay_status = classify_existing_generation(existing, manifest)
                if replay_status is not None:
                    return {
                        **manifest,
                        'status': replay_status,
                        'target_state': 'READY' if replay_status == 'ALREADY_READY' else 'BUILDING',
                        'loaded_sessions': int(manifest['session_count']),
                        'loaded_messages': int(manifest['message_count']),
                    }

                if len(sessions) != int(manifest['session_count']):
                    raise TargetMaterializationError('BUNDLE_SESSION_COUNT_MISMATCH')
                if len(messages) != int(manifest['message_count']):
                    raise TargetMaterializationError('BUNDLE_MESSAGE_COUNT_MISMATCH')

                cur.execute(
                    "INSERT INTO session_search.generations("
                    "generation_id,shard_id,provider,source_manifest_sha256,projection_schema_version"
                    ") VALUES (%s,%s,%s,%s,%s)",
                    (
                        manifest['generation_id'], manifest['shard_id'], manifest['provider'],
                        manifest['source_manifest_sha256'], manifest['projection_schema_version'],
                    ),
                )
                execute_values_fn(
                    cur,
                    "INSERT INTO session_search.sessions("
                    "generation_id,session_id,title,coverage_state,time_min,time_max"
                    ") VALUES %s",
                    [(manifest['generation_id'], *row) for row in sessions],
                    page_size=batch_size,
                )
                for start in range(0, len(messages), batch_size):
                    page = [
                        (manifest['generation_id'], *row)
                        for row in messages[start:start + batch_size]
                    ]
                    execute_values_fn(
                        cur,
                        "INSERT INTO session_search.messages("
                        "generation_id,session_id,ordinal,message_id,role,search_class,create_time,text"
                        ") VALUES %s",
                        page,
                        page_size=batch_size,
                    )
                cur.execute(
                    "SELECT state,"
                    "(SELECT count(*) FROM session_search.sessions WHERE generation_id=%s),"
                    "(SELECT count(*) FROM session_search.messages WHERE generation_id=%s) "
                    "FROM session_search.generations WHERE generation_id=%s",
                    (manifest['generation_id'], manifest['generation_id'], manifest['generation_id']),
                )
                observed = cur.fetchone()
                if observed is None:
                    raise TargetMaterializationError('TARGET_GENERATION_READBACK_MISSING')
                state, session_count, message_count = observed
                if state != 'BUILDING':
                    raise TargetMaterializationError('TARGET_GENERATION_NOT_BUILDING')
                if int(session_count) != int(manifest['session_count']):
                    raise TargetMaterializationError('TARGET_SESSION_COUNT_MISMATCH')
                if int(message_count) != int(manifest['message_count']):
                    raise TargetMaterializationError('TARGET_MESSAGE_COUNT_MISMATCH')
    finally:
        conn.close()

    return {
        **manifest,
        'status': 'LOADED',
        'target_state': 'BUILDING',
        'loaded_sessions': int(manifest['session_count']),
        'loaded_messages': int(manifest['message_count']),
    }
