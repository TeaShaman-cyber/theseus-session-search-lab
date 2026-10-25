#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any

HEX64 = re.compile(r'^[0-9a-f]{64}$')
DRIVE_FILE_ID = re.compile(r'^[A-Za-z0-9_-]{10,200}$')
PROJECT_ID = re.compile(r'^[a-z0-9-]{1,60}$')
BRANCH_ID = re.compile(r'^br-[a-z0-9-]{1,60}$')
DATABASE_NAME = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}$')
BUNDLE_SCHEMA = 'theseus.session-search-shard-bundle.v1'
TOOLKITS = ('session_search_materializer', 'SessionSearchMaterializer', 'session-search-materializer')
ACCEPTED_STATUSES = {'LOADED', 'ALREADY_BUILDING', 'ALREADY_READY'}


class MaterializeOpsError(RuntimeError):
    pass


def _qualified_name(tool: Any) -> str:
    return getattr(tool, 'qualified_name', '') or getattr(tool, 'fully_qualified_name', '') or ''


def _is_materialize_tool(name: str) -> bool:
    return re.sub(r'[^a-z0-9]', '', name.lower()).endswith('materializebundle')


def _collect(client: Any, toolkit: str | None) -> list[str]:
    matches: list[str] = []
    offset = None
    while True:
        kwargs: dict[str, Any] = {'limit': 100}
        if toolkit is not None:
            kwargs['toolkit'] = toolkit
        if offset:
            kwargs['offset'] = offset
        try:
            page = client.tools.list(**kwargs)
        except Exception as exc:
            if offset is not None or matches:
                raise MaterializeOpsError('MATERIALIZE_BUNDLE_DISCOVERY_INCOMPLETE') from exc
            return matches
        for tool in page.items:
            name = _qualified_name(tool)
            if name and _is_materialize_tool(name):
                matches.append(name)
        offset = getattr(page, 'next_offset', None)
        if not offset:
            break
    return matches


def discover_materialize_bundle(client: Any) -> str:
    matches: list[str] = []
    for toolkit in TOOLKITS:
        matches.extend(_collect(client, toolkit))
    if not matches:
        matches.extend(_collect(client, None))
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise MaterializeOpsError(
            f'MATERIALIZE_BUNDLE_DISCOVERY_FAILED count={len(unique)} names={unique}'
        )
    return unique[0]


def _normalize(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MaterializeOpsError('MATERIALIZE_TOOL_OUTPUT_NOT_JSON') from exc
    if not isinstance(value, dict):
        raise MaterializeOpsError('MATERIALIZE_TOOL_OUTPUT_NOT_OBJECT')
    return value


def execute_materialize_bundle(
    client: Any,
    *,
    user_id: str,
    drive_file_id: str,
    expected_bundle_sha256: str,
    neon_project_id: str,
    neon_branch_id: str,
    neon_database_name: str,
) -> dict[str, Any]:
    tool_name = discover_materialize_bundle(client)
    auth = client.tools.authorize(tool_name=tool_name, user_id=user_id)
    if getattr(auth, 'status', None) != 'completed':
        raise MaterializeOpsError('MATERIALIZE_TOOL_AUTH_NOT_COMPLETED')

    response = client.tools.execute(
        tool_name=tool_name,
        input={
            'drive_file_id': drive_file_id,
            'expected_bundle_sha256': expected_bundle_sha256,
            'neon_project_id': neon_project_id,
            'neon_branch_id': neon_branch_id,
            'neon_database_name': neon_database_name,
        },
        user_id=user_id,
    )
    if not getattr(response, 'success', False):
        error = getattr(getattr(response, 'output', None), 'error', None)
        kind = getattr(error, 'kind', None) or 'UNKNOWN'
        message = getattr(error, 'message', None) or 'tool execution failed'
        raise MaterializeOpsError(f'MATERIALIZE_TOOL_FAILED kind={kind} message={message}')

    payload = _normalize(getattr(getattr(response, 'output', None), 'value', None))
    mismatches: dict[str, Any] = {}
    expected = {
        'schema': BUNDLE_SCHEMA,
        'bundle_sha256': expected_bundle_sha256,
        'neon_project_id': neon_project_id,
        'neon_branch_id': neon_branch_id,
        'neon_database_name': neon_database_name,
        'loader_role': 'session_search_arcade_loader_sql',
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            mismatches[key] = {'expected': value, 'observed': payload.get(key)}
    status = payload.get('status')
    if status not in ACCEPTED_STATUSES:
        mismatches['status'] = {'expected_one_of': sorted(ACCEPTED_STATUSES), 'observed': status}
    expected_state = 'READY' if status == 'ALREADY_READY' else 'BUILDING'
    if payload.get('target_state') != expected_state:
        mismatches['target_state'] = {'expected': expected_state, 'observed': payload.get('target_state')}
    if payload.get('loaded_sessions') != payload.get('session_count'):
        mismatches['loaded_sessions'] = {
            'expected': payload.get('session_count'), 'observed': payload.get('loaded_sessions')
        }
    if payload.get('loaded_messages') != payload.get('message_count'):
        mismatches['loaded_messages'] = {
            'expected': payload.get('message_count'), 'observed': payload.get('loaded_messages')
        }
    if mismatches:
        raise MaterializeOpsError(
            f'MATERIALIZE_TOOL_POSTCONDITION_MISMATCH {json.dumps(mismatches, sort_keys=True)}'
        )

    return {
        'tool_name': tool_name,
        'execution_id': getattr(response, 'id', None) or getattr(response, 'execution_id', None),
        **payload,
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Load one exact verified Session Search Drive bundle into a Neon BUILDING generation through Arcade.')
    p.add_argument('--drive-file-id', required=True)
    p.add_argument('--expected-bundle-sha256', required=True)
    p.add_argument('--neon-project-id', required=True)
    p.add_argument('--neon-branch-id', required=True)
    p.add_argument('--neon-database-name', default='neondb')
    return p


def _validate(args: argparse.Namespace) -> None:
    if not DRIVE_FILE_ID.fullmatch(args.drive_file_id):
        raise MaterializeOpsError('DRIVE_FILE_ID_INVALID')
    if not HEX64.fullmatch(args.expected_bundle_sha256):
        raise MaterializeOpsError('EXPECTED_BUNDLE_SHA256_INVALID')
    if not PROJECT_ID.fullmatch(args.neon_project_id):
        raise MaterializeOpsError('NEON_PROJECT_ID_INVALID')
    if not BRANCH_ID.fullmatch(args.neon_branch_id):
        raise MaterializeOpsError('NEON_BRANCH_ID_INVALID')
    if not DATABASE_NAME.fullmatch(args.neon_database_name):
        raise MaterializeOpsError('NEON_DATABASE_NAME_INVALID')


def main() -> int:
    args = _parser().parse_args()
    _validate(args)
    required_env = ('ARCADE_URL', 'ARCADE_API_KEY', 'ARCADE_USER_ID')
    missing = [name for name in required_env if not os.environ.get(name)]
    if missing:
        raise MaterializeOpsError('MISSING_ARCADE_ENV ' + ','.join(missing))

    from arcadepy import Arcade

    client = Arcade(api_key=os.environ['ARCADE_API_KEY'], base_url=os.environ['ARCADE_URL'])
    result = execute_materialize_bundle(
        client,
        user_id=os.environ['ARCADE_USER_ID'],
        drive_file_id=args.drive_file_id,
        expected_bundle_sha256=args.expected_bundle_sha256,
        neon_project_id=args.neon_project_id,
        neon_branch_id=args.neon_branch_id,
        neon_database_name=args.neon_database_name,
    )
    print('ARCADE_MATERIALIZE_BUNDLE_PASS')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
