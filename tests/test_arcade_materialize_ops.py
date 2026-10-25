import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))

from arcade_materialize_bundle import (
    MaterializeOpsError,
    discover_materialize_bundle,
    execute_materialize_bundle,
)


class FakeTools:
    def __init__(self, *, output=None, auth_status='completed', success=True):
        self.output = output
        self.auth_status = auth_status
        self.success = success
        self.executed = None
        self.calls = 0
        self.fail_second_page = False

    def list(self, **kwargs):
        self.calls += 1
        if self.fail_second_page and kwargs.get('offset'):
            raise RuntimeError('page failure')
        if kwargs.get('toolkit') == 'session_search_materializer':
            return SimpleNamespace(
                items=[SimpleNamespace(qualified_name='SessionSearchMaterializer.MaterializeBundle')],
                next_offset='next' if self.fail_second_page and not kwargs.get('offset') else None,
            )
        return SimpleNamespace(items=[], next_offset=None)

    def authorize(self, *, tool_name, user_id):
        return SimpleNamespace(status=self.auth_status)

    def execute(self, *, tool_name, input, user_id):
        self.executed = (tool_name, input, user_id)
        return SimpleNamespace(
            success=self.success,
            id='te_materialize_1',
            output=SimpleNamespace(value=self.output, error=None),
        )


class FakeClient:
    def __init__(self, tools):
        self.tools = tools


class ArcadeMaterializeOpsTest(unittest.TestCase):
    def test_discovers_materialize_bundle(self):
        tools = FakeTools()
        self.assertEqual(
            discover_materialize_bundle(FakeClient(tools)),
            'SessionSearchMaterializer.MaterializeBundle',
        )

    def test_partial_paginated_discovery_fails_closed(self):
        tools = FakeTools(); tools.fail_second_page = True
        with self.assertRaisesRegex(MaterializeOpsError, 'MATERIALIZE_BUNDLE_DISCOVERY_INCOMPLETE'):
            discover_materialize_bundle(FakeClient(tools))

    def test_exact_building_load_contract(self):
        sha = 'a' * 64
        output = {
            'status': 'LOADED',
            'target_state': 'BUILDING',
            'schema': 'theseus.session-search-shard-bundle.v1',
            'bundle_sha256': sha,
            'provider': 'deepseek',
            'shard_id': 'deepseek-0001',
            'generation_id': 'b' * 64,
            'session_count': 43,
            'message_count': 64662,
            'loaded_sessions': 43,
            'loaded_messages': 64662,
            'neon_project_id': 'wild-surf-61625738',
            'neon_branch_id': 'br-floral-morning-b5kactgx',
            'neon_database_name': 'neondb',
            'loader_role': 'session_search_arcade_loader_sql',
        }
        tools = FakeTools(output=output)
        result = execute_materialize_bundle(
            FakeClient(tools), user_id='operator',
            drive_file_id='1skemXc1FG_G6oh3VbHYV_BiAbv-iIF30',
            expected_bundle_sha256=sha,
            neon_project_id='wild-surf-61625738',
            neon_branch_id='br-floral-morning-b5kactgx',
            neon_database_name='neondb',
        )
        self.assertEqual(result['status'], 'LOADED')
        self.assertEqual(result['execution_id'], 'te_materialize_1')
        sent = tools.executed[1]
        self.assertEqual(set(sent), {
            'drive_file_id', 'expected_bundle_sha256', 'neon_project_id',
            'neon_branch_id', 'neon_database_name',
        })
        self.assertNotIn('ready', sent)
        self.assertNotIn('activate', sent)
        self.assertNotIn('catalog', sent)

    def test_exact_building_replay_is_accepted(self):
        sha = 'a' * 64
        output = {
            'status': 'ALREADY_BUILDING', 'target_state': 'BUILDING',
            'schema': 'theseus.session-search-shard-bundle.v1', 'bundle_sha256': sha,
            'session_count': 43, 'message_count': 64662,
            'loaded_sessions': 43, 'loaded_messages': 64662,
            'neon_project_id': 'wild-surf-61625738',
            'neon_branch_id': 'br-floral-morning-b5kactgx',
            'neon_database_name': 'neondb',
            'loader_role': 'session_search_arcade_loader_sql',
        }
        result = execute_materialize_bundle(
            FakeClient(FakeTools(output=output)), user_id='operator',
            drive_file_id='1skemXc1FG_G6oh3VbHYV_BiAbv-iIF30',
            expected_bundle_sha256=sha,
            neon_project_id='wild-surf-61625738',
            neon_branch_id='br-floral-morning-b5kactgx',
            neon_database_name='neondb',
        )
        self.assertEqual(result['status'], 'ALREADY_BUILDING')

    def test_unfinished_google_auth_fails(self):
        tools = FakeTools(auth_status='pending')
        with self.assertRaisesRegex(MaterializeOpsError, 'MATERIALIZE_TOOL_AUTH_NOT_COMPLETED'):
            execute_materialize_bundle(
                FakeClient(tools), user_id='operator', drive_file_id='1skemXc1FG_G6oh3VbHYV_BiAbv-iIF30',
                expected_bundle_sha256='a' * 64, neon_project_id='wild-surf-61625738',
                neon_branch_id='br-floral-morning-b5kactgx', neon_database_name='neondb',
            )

    def test_wrong_target_readback_fails(self):
        sha = 'a' * 64
        output = {
            'status': 'LOADED', 'target_state': 'BUILDING',
            'schema': 'theseus.session-search-shard-bundle.v1', 'bundle_sha256': sha,
            'session_count': 43, 'message_count': 64662,
            'loaded_sessions': 43, 'loaded_messages': 64662,
            'neon_project_id': 'wrong-project',
            'neon_branch_id': 'br-floral-morning-b5kactgx',
            'neon_database_name': 'neondb',
            'loader_role': 'session_search_arcade_loader_sql',
        }
        with self.assertRaisesRegex(MaterializeOpsError, 'MATERIALIZE_TOOL_POSTCONDITION_MISMATCH'):
            execute_materialize_bundle(
                FakeClient(FakeTools(output=output)), user_id='operator',
                drive_file_id='1skemXc1FG_G6oh3VbHYV_BiAbv-iIF30', expected_bundle_sha256=sha,
                neon_project_id='wild-surf-61625738', neon_branch_id='br-floral-morning-b5kactgx',
                neon_database_name='neondb',
            )


if __name__ == '__main__':
    unittest.main()
