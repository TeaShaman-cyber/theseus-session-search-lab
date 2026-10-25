#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any

HEX64 = re.compile(r'^[0-9a-f]{64}$')
PROJECT_ID = re.compile(r'^[a-z0-9-]{1,60}$')
BRANCH_ID = re.compile(r'^br-[a-z0-9-]{1,60}$')
DATABASE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}$')


class ReadyOpsError(RuntimeError):
    pass


def _qualified_name(tool: Any) -> str:
    return (
        getattr(tool, 'qualified_name', '')
        or getattr(tool, 'fully_qualified_name', '')
        or ''
    )


def _is_mark_bundle_ready(name: str) -> bool:
    compact = re.sub(r'[^a-z0-9]', '', name.lower())
    return compact.endswith('markbundleready')


READY_TOOLKITS = (
    'session_search_materializer',
    'SessionSearchMaterializer',
    'session-search-materializer',
)


def _collect_ready_matches(client: Any, *, toolkit: str | None) -> list[str]:
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
                raise ReadyOpsError('MARK_BUNDLE_READY_DISCOVERY_INCOMPLETE') from exc
            return matches
        for tool in page.items:
            name = _qualified_name(tool)
            if name and _is_mark_bundle_ready(name):
                matches.append(name)
        offset = getattr(page, 'next_offset', None)
        if not offset:
            break
    return matches


def discover_mark_bundle_ready(client: Any) -> str:
    matches: list[str] = []
    for toolkit in READY_TOOLKITS:
        matches.extend(_collect_ready_matches(client, toolkit=toolkit))
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise ReadyOpsError(
            f'MARK_BUNDLE_READY_DISCOVERY_FAILED count={len(unique)} names={unique}'
        )
    return unique[0]


def _normalize_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ReadyOpsError('READY_TOOL_OUTPUT_NOT_JSON') from exc
    if not isinstance(value, dict):
        raise ReadyOpsError('READY_TOOL_OUTPUT_NOT_OBJECT')
    return value


def execute_mark_bundle_ready(
    client: Any,
    *,
    user_id: str,
    neon_project_id: str,
    neon_branch_id: str,
    generation_id: str,
    expected_source_manifest_sha256: str,
    expected_session_count: int,
    expected_message_count: int,
    neon_database_name: str,
) -> dict[str, Any]:
    tool_name = discover_mark_bundle_ready(client)
    auth = client.tools.authorize(tool_name=tool_name, user_id=user_id)
    if getattr(auth, 'status', None) != 'completed':
        raise ReadyOpsError('READY_TOOL_UNEXPECTED_AUTH_REQUIREMENT')

    tool_input = {
        'neon_project_id': neon_project_id,
        'neon_branch_id': neon_branch_id,
        'generation_id': generation_id,
        'expected_source_manifest_sha256': expected_source_manifest_sha256,
        'expected_session_count': expected_session_count,
        'expected_message_count': expected_message_count,
        'neon_database_name': neon_database_name,
    }
    response = client.tools.execute(
        tool_name=tool_name,
        input=tool_input,
        user_id=user_id,
    )
    if not getattr(response, 'success', False):
        error = getattr(getattr(response, 'output', None), 'error', None)
        kind = getattr(error, 'kind', None) or 'UNKNOWN'
        message = getattr(error, 'message', None) or 'tool execution failed'
        raise ReadyOpsError(f'READY_TOOL_FAILED kind={kind} message={message}')

    payload = _normalize_payload(getattr(getattr(response, 'output', None), 'value', None))
    expected = {
        'status': 'READY',
        'target_state': 'READY',
        'generation_id': generation_id,
        'source_manifest_sha256': expected_source_manifest_sha256,
        'session_count': expected_session_count,
        'message_count': expected_message_count,
        'neon_project_id': neon_project_id,
        'neon_branch_id': neon_branch_id,
        'neon_database_name': neon_database_name,
    }
    mismatches = {
        key: {'expected': value, 'observed': payload.get(key)}
        for key, value in expected.items()
        if payload.get(key) != value
    }
    if mismatches:
        raise ReadyOpsError(f'READY_TOOL_POSTCONDITION_MISMATCH {json.dumps(mismatches, sort_keys=True)}')

    return {
        'tool_name': tool_name,
        'execution_id': getattr(response, 'id', None) or getattr(response, 'execution_id', None),
        **expected,
    }


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError('must be >= 1')
    return value


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Promote one exact Session Search generation from BUILDING to READY through Arcade.')
    p.add_argument('--neon-project-id', required=True)
    p.add_argument('--neon-branch-id', required=True)
    p.add_argument('--generation-id', required=True)
    p.add_argument('--expected-source-manifest-sha256', required=True)
    p.add_argument('--expected-session-count', required=True, type=_positive_int)
    p.add_argument('--expected-message-count', required=True, type=_positive_int)
    p.add_argument('--neon-database-name', default='neondb')
    return p


def _validate(args: argparse.Namespace) -> None:
    if not PROJECT_ID.fullmatch(args.neon_project_id):
        raise ReadyOpsError('NEON_PROJECT_ID_INVALID')
    if not BRANCH_ID.fullmatch(args.neon_branch_id):
        raise ReadyOpsError('NEON_BRANCH_ID_INVALID')
    if not HEX64.fullmatch(args.generation_id):
        raise ReadyOpsError('GENERATION_ID_INVALID')
    if not HEX64.fullmatch(args.expected_source_manifest_sha256):
        raise ReadyOpsError('SOURCE_MANIFEST_SHA256_INVALID')
    if not DATABASE.fullmatch(args.neon_database_name):
        raise ReadyOpsError('NEON_DATABASE_NAME_INVALID')


def main() -> int:
    args = _parser().parse_args()
    _validate(args)
    required_env = ('ARCADE_URL', 'ARCADE_API_KEY', 'ARCADE_USER_ID')
    missing = [name for name in required_env if not os.environ.get(name)]
    if missing:
        raise ReadyOpsError('MISSING_ARCADE_ENV ' + ','.join(missing))

    from arcadepy import Arcade

    client = Arcade(
        api_key=os.environ['ARCADE_API_KEY'],
        base_url=os.environ['ARCADE_URL'],
    )
    result = execute_mark_bundle_ready(
        client,
        user_id=os.environ['ARCADE_USER_ID'],
        neon_project_id=args.neon_project_id,
        neon_branch_id=args.neon_branch_id,
        generation_id=args.generation_id,
        expected_source_manifest_sha256=args.expected_source_manifest_sha256,
        expected_session_count=args.expected_session_count,
        expected_message_count=args.expected_message_count,
        neon_database_name=args.neon_database_name,
    )
    print('ARCADE_READY_PROMOTION_PASS')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
