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
TOOLKITS = ('session_search_materializer', 'SessionSearchMaterializer', 'session-search-materializer')


class ActivationOpsError(RuntimeError):
    pass


def _qualified_name(tool: Any) -> str:
    return getattr(tool, 'qualified_name', '') or getattr(tool, 'fully_qualified_name', '') or ''


def _is_activation_tool(name: str) -> bool:
    return re.sub(r'[^a-z0-9]', '', name.lower()).endswith('activatereadygeneration')


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
                raise ActivationOpsError('ACTIVATION_TOOL_DISCOVERY_INCOMPLETE') from exc
            return matches
        for tool in page.items:
            name = _qualified_name(tool)
            if name and _is_activation_tool(name):
                matches.append(name)
        offset = getattr(page, 'next_offset', None)
        if not offset:
            break
    return matches


def discover_activation_tool(client: Any) -> str:
    matches: list[str] = []
    for toolkit in TOOLKITS:
        matches.extend(_collect(client, toolkit))
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise ActivationOpsError(f'ACTIVATION_TOOL_DISCOVERY_FAILED count={len(unique)} names={unique}')
    return unique[0]


def _normalize(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ActivationOpsError('ACTIVATION_TOOL_OUTPUT_NOT_JSON') from exc
    if not isinstance(value, dict):
        raise ActivationOpsError('ACTIVATION_TOOL_OUTPUT_NOT_OBJECT')
    return value


def execute_activation(
    client: Any,
    *,
    user_id: str,
    neon_project_id: str,
    neon_branch_id: str,
    generation_id: str,
    expected_previous_generation_id: str,
    neon_database_name: str,
) -> dict[str, Any]:
    tool_name = discover_activation_tool(client)
    auth = client.tools.authorize(tool_name=tool_name, user_id=user_id)
    if getattr(auth, 'status', None) != 'completed':
        raise ActivationOpsError('ACTIVATION_TOOL_UNEXPECTED_AUTH_REQUIREMENT')
    response = client.tools.execute(
        tool_name=tool_name,
        input={
            'neon_project_id': neon_project_id,
            'neon_branch_id': neon_branch_id,
            'generation_id': generation_id,
            'expected_previous_generation_id': expected_previous_generation_id,
            'neon_database_name': neon_database_name,
        },
        user_id=user_id,
    )
    if not getattr(response, 'success', False):
        error = getattr(getattr(response, 'output', None), 'error', None)
        kind = getattr(error, 'kind', None) or 'UNKNOWN'
        message = getattr(error, 'message', None) or 'tool execution failed'
        raise ActivationOpsError(f'ACTIVATION_TOOL_FAILED kind={kind} message={message}')
    payload = _normalize(getattr(getattr(response, 'output', None), 'value', None))
    expected_previous = None if expected_previous_generation_id == 'NULL' else expected_previous_generation_id
    expected = {
        'status': 'ACTIVATED',
        'generation_id': generation_id,
        'expected_previous_generation_id': expected_previous,
        'active_generation': generation_id,
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
        raise ActivationOpsError(f'ACTIVATION_TOOL_POSTCONDITION_MISMATCH {json.dumps(mismatches, sort_keys=True)}')
    return {
        'tool_name': tool_name,
        'execution_id': getattr(response, 'id', None) or getattr(response, 'execution_id', None),
        **expected,
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='CAS-activate one exact READY Session Search generation through Arcade.')
    p.add_argument('--neon-project-id', required=True)
    p.add_argument('--neon-branch-id', required=True)
    p.add_argument('--generation-id', required=True)
    p.add_argument('--expected-previous-generation-id', required=True)
    p.add_argument('--neon-database-name', default='neondb')
    return p


def _validate(args: argparse.Namespace) -> None:
    if not PROJECT_ID.fullmatch(args.neon_project_id): raise ActivationOpsError('NEON_PROJECT_ID_INVALID')
    if not BRANCH_ID.fullmatch(args.neon_branch_id): raise ActivationOpsError('NEON_BRANCH_ID_INVALID')
    if not HEX64.fullmatch(args.generation_id): raise ActivationOpsError('GENERATION_ID_INVALID')
    if args.expected_previous_generation_id != 'NULL' and not HEX64.fullmatch(args.expected_previous_generation_id):
        raise ActivationOpsError('EXPECTED_PREVIOUS_GENERATION_ID_INVALID')
    if not DATABASE.fullmatch(args.neon_database_name): raise ActivationOpsError('NEON_DATABASE_NAME_INVALID')


def main() -> int:
    args = _parser().parse_args()
    _validate(args)
    required_env = ('ARCADE_URL', 'ARCADE_API_KEY', 'ARCADE_USER_ID')
    missing = [name for name in required_env if not os.environ.get(name)]
    if missing:
        raise ActivationOpsError('MISSING_ARCADE_ENV ' + ','.join(missing))
    from arcadepy import Arcade
    client = Arcade(api_key=os.environ['ARCADE_API_KEY'], base_url=os.environ['ARCADE_URL'])
    result = execute_activation(
        client,
        user_id=os.environ['ARCADE_USER_ID'],
        neon_project_id=args.neon_project_id,
        neon_branch_id=args.neon_branch_id,
        generation_id=args.generation_id,
        expected_previous_generation_id=args.expected_previous_generation_id,
        neon_database_name=args.neon_database_name,
    )
    print('ARCADE_ACTIVATION_PASS')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
