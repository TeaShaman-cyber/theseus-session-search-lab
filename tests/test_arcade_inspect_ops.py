import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))

from arcade_inspect_bundle import InspectOpsError, discover_inspect_bundle, execute_inspect_bundle


class FakeTools:
    def __init__(self, *, names=None, scoped_names=None, output=None, auth_status='completed', success=True):
        self.names = names or []
        self.scoped_names = scoped_names or {}
        self.output = output
        self.auth_status = auth_status
        self.success = success
        self.executed = None
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        toolkit = kwargs.get('toolkit')
        if getattr(self, 'fail_on_offset', False) and kwargs.get('offset'):
            raise RuntimeError('page failure')
        names = self.scoped_names.get(toolkit, self.names if toolkit is None else [])
        return SimpleNamespace(items=[SimpleNamespace(qualified_name=n) for n in names], next_offset=None)

    def authorize(self, *, tool_name, user_id):
        return SimpleNamespace(status=self.auth_status, url='https://example.invalid/auth')

    def execute(self, *, tool_name, input, user_id):
        self.executed = (tool_name, input, user_id)
        return SimpleNamespace(
            success=self.success,
            id='te_inspect_1',
            output=SimpleNamespace(value=self.output, error=None),
        )


class FakeClient:
    def __init__(self, tools):
        self.tools = tools


class ArcadeInspectOpsTest(unittest.TestCase):
    def test_discovers_inspector_from_hosted_server_scope(self):
        tools = FakeTools(scoped_names={
            'session_search_materializer': ['SessionSearchMaterializer.InspectBundle']
        })
        self.assertEqual(
            discover_inspect_bundle(FakeClient(tools)),
            'SessionSearchMaterializer.InspectBundle',
        )
        self.assertEqual(tools.list_calls[0]['toolkit'], 'session_search_materializer')

    def test_unscoped_lookalike_is_never_selected(self):
        tools = FakeTools(names=['OtherToolkit.InspectBundle'])
        with self.assertRaisesRegex(InspectOpsError, 'INSPECT_BUNDLE_DISCOVERY_FAILED'):
            discover_inspect_bundle(FakeClient(tools))
        self.assertFalse(any(call.get('toolkit') is None for call in tools.list_calls))

    def test_partial_paginated_discovery_fails_closed(self):
        tools = FakeTools(names=[])
        calls = 0
        original_list = tools.list
        def paged_list(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SimpleNamespace(items=[SimpleNamespace(qualified_name='SessionSearchMaterializer.InspectBundle')], next_offset='next')
            raise RuntimeError('page failure')
        tools.list = paged_list
        with self.assertRaisesRegex(InspectOpsError, 'INSPECT_BUNDLE_DISCOVERY_INCOMPLETE'):
            discover_inspect_bundle(FakeClient(tools))

    def test_ambiguous_discovery_fails_closed(self):
        tools = FakeTools(names=['A.InspectBundle', 'B.inspect_bundle'])
        with self.assertRaisesRegex(InspectOpsError, 'INSPECT_BUNDLE_DISCOVERY_FAILED'):
            discover_inspect_bundle(FakeClient(tools))

    def test_executes_read_only_exact_file_contract(self):
        sha = 'a' * 64
        output = {
            'status': 'VERIFIED',
            'schema': 'theseus.session-search-shard-bundle.v1',
            'provider': 'deepseek',
            'shard_id': 'deepseek-0001',
            'generation_id': 'b' * 64,
            'source_manifest_sha256': 'c' * 64,
            'source_projection_sha256': 'd' * 64,
            'accepted_artifact_count': 737,
            'accepted_set_sha256': 'e' * 64,
            'session_count': 43,
            'message_count': 64662,
            'searchable_text_bytes': 114332170,
            'bundle_sha256': sha,
            'bundle_size_bytes': 119696285,
            'projection_schema_version': 'session-search-neon-v1',
        }
        tools = FakeTools(
            scoped_names={'session_search_materializer': ['SessionSearchMaterializer.InspectBundle']},
            output=output,
        )
        result = execute_inspect_bundle(
            FakeClient(tools), user_id='operator',
            drive_file_id='1skemXc1FG_G6oh3VbHYV_BiAbv-iIF30',
            expected_bundle_sha256=sha,
        )
        self.assertEqual(result['status'], 'VERIFIED')
        self.assertEqual(result['execution_id'], 'te_inspect_1')
        sent = tools.executed[1]
        self.assertEqual(set(sent), {'drive_file_id', 'expected_bundle_sha256'})
        self.assertNotIn('neon_project_id', sent)
        self.assertNotIn('activate', sent)
        self.assertNotIn('catalog', sent)

    def test_output_sha_mismatch_fails_closed(self):
        tools = FakeTools(
            scoped_names={'session_search_materializer': ['SessionSearchMaterializer.InspectBundle']},
            output={
                'status': 'VERIFIED',
                'schema': 'theseus.session-search-shard-bundle.v1',
                'bundle_sha256': 'b' * 64,
            },
        )
        with self.assertRaisesRegex(InspectOpsError, 'INSPECT_TOOL_POSTCONDITION_MISMATCH'):
            execute_inspect_bundle(
                FakeClient(tools), user_id='operator',
                drive_file_id='1skemXc1FG_G6oh3VbHYV_BiAbv-iIF30',
                expected_bundle_sha256='a' * 64,
            )

    def test_unfinished_google_auth_fails_without_manual_flow(self):
        tools = FakeTools(
            scoped_names={'session_search_materializer': ['SessionSearchMaterializer.InspectBundle']},
            auth_status='pending',
        )
        with self.assertRaisesRegex(InspectOpsError, 'INSPECT_TOOL_AUTH_NOT_COMPLETED'):
            execute_inspect_bundle(
                FakeClient(tools), user_id='operator',
                drive_file_id='1skemXc1FG_G6oh3VbHYV_BiAbv-iIF30',
                expected_bundle_sha256='a' * 64,
            )


if __name__ == '__main__':
    unittest.main()
