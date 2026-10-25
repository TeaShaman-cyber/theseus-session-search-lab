#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any

HEX64 = re.compile(r'^[0-9a-f]{64}$')
DRIVE_FILE_ID = re.compile(r'^[A-Za-z0-9_-]{10,200}$')
BUNDLE_SCHEMA = 'theseus.session-search-shard-bundle.v1'
TOOLKITS = ('session_search_materializer', 'SessionSearchMaterializer', 'session-search-materializer')


class InspectOpsError(RuntimeError):
    pass


def _qualified_name(tool: Any) -> str:
    return getattr(tool, 'qualified_name', '') or getattr(tool, 'fully_qualified_name', '') or ''


def _is_inspect_tool(name: str) -> bool:
    return re.sub(r'[^a-z0-9]', '', name.lower()).endswith('inspectbundle')


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
                raise InspectOpsError('INSPECT_BUNDLE_DISCOVERY_INCOMPLETE') from exc
            return matches
        for tool in page.items:
            name = _qualified_name(tool)
            if name and _is_inspect_tool(name):
                matches.append(name)
        offset = getattr(page, 'next_offset', None)
        if not offset:
            break
    return matches


def discover_inspect_bundle(client: Any) -> str:
    matches: list[str] = []
    for toolkit in TOOLKITS:
        matches.extend(_collect(client, toolkit))
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise InspectOpsError(
            f'INSPECT_BUNDLE_DISCOVERY_FAILED count={len(unique)} names={unique}'
        )
    return unique[0]


def _normalize(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise InspectOpsError('INSPECT_TOOL_OUTPUT_NOT_JSON') from exc
    if not isinstance(value, dict):
        raise InspectOpsError('INSPECT_TOOL_OUTPUT_NOT_OBJECT')
    return value


def execute_inspect_bundle(
    client: Any,
    *,
    user_id: str,
    drive_file_id: str,
    expected_bundle_sha256: str,
) -> dict[str, Any]:
    tool_name = discover_inspect_bundle(client)
    auth = client.tools.authorize(tool_name=tool_name, user_id=user_id)
    if getattr(auth, 'status', None) != 'completed':
        raise InspectOpsError('INSPECT_TOOL_AUTH_NOT_COMPLETED')

    response = client.tools.execute(
        tool_name=tool_name,
        input={
            'drive_file_id': drive_file_id,
            'expected_bundle_sha256': expected_bundle_sha256,
        },
        user_id=user_id,
    )
    if not getattr(response, 'success', False):
        error = getattr(getattr(response, 'output', None), 'error', None)
        kind = getattr(error, 'kind', None) or 'UNKNOWN'
        message = getattr(error, 'message', None) or 'tool execution failed'
        raise InspectOpsError(f'INSPECT_TOOL_FAILED kind={kind} message={message}')

    payload = _normalize(getattr(getattr(response, 'output', None), 'value', None))
    expected = {
        'status': 'VERIFIED',
        'schema': BUNDLE_SCHEMA,
        'bundle_sha256': expected_bundle_sha256,
    }
    mismatches = {
        key: {'expected': value, 'observed': payload.get(key)}
        for key, value in expected.items()
        if payload.get(key) != value
    }
    if mismatches:
        raise InspectOpsError(
            f'INSPECT_TOOL_POSTCONDITION_MISMATCH {json.dumps(mismatches, sort_keys=True)}'
        )

    return {
        'tool_name': tool_name,
        'execution_id': getattr(response, 'id', None) or getattr(response, 'execution_id', None),
        **payload,
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Verify one exact private Session Search Drive bundle through Arcade InspectBundle.'
    )
    p.add_argument('--drive-file-id', required=True)
    p.add_argument('--expected-bundle-sha256', required=True)
    return p


def _validate(args: argparse.Namespace) -> None:
    if not DRIVE_FILE_ID.fullmatch(args.drive_file_id):
        raise InspectOpsError('DRIVE_FILE_ID_INVALID')
    if not HEX64.fullmatch(args.expected_bundle_sha256):
        raise InspectOpsError('EXPECTED_BUNDLE_SHA256_INVALID')


def main() -> int:
    args = _parser().parse_args()
    _validate(args)
    required_env = ('ARCADE_URL', 'ARCADE_API_KEY', 'ARCADE_USER_ID')
    missing = [name for name in required_env if not os.environ.get(name)]
    if missing:
        raise InspectOpsError('MISSING_ARCADE_ENV ' + ','.join(missing))

    from arcadepy import Arcade

    client = Arcade(api_key=os.environ['ARCADE_API_KEY'], base_url=os.environ['ARCADE_URL'])
    result = execute_inspect_bundle(
        client,
        user_id=os.environ['ARCADE_USER_ID'],
        drive_file_id=args.drive_file_id,
        expected_bundle_sha256=args.expected_bundle_sha256,
    )
    print('ARCADE_INSPECT_BUNDLE_PASS')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
