import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))

from arcade_mark_bundle_ready import ReadyOpsError, discover_mark_bundle_ready, execute_mark_bundle_ready


class FakeTools:
    def __init__(self, *, names, output=None, auth_status='completed', success=True, scoped_names=None):
        self.names = names
        self.scoped_names = scoped_names or {}
        self.output = output
        self.auth_status = auth_status
        self.success = success
        self.executed = None
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        toolkit = kwargs.get('toolkit')
        names = self.scoped_names.get(toolkit, self.names if toolkit is None else [])
        items = [SimpleNamespace(qualified_name=name) for name in names]
        return SimpleNamespace(items=items, next_offset=None)

    def authorize(self, *, tool_name, user_id):
        self.authorized = (tool_name, user_id)
        return SimpleNamespace(status=self.auth_status, url='https://example.invalid/auth')

    def execute(self, *, tool_name, input, user_id):
        self.executed = (tool_name, input, user_id)
        return SimpleNamespace(
            success=self.success,
            id='tc_ready_1',
            output=SimpleNamespace(value=self.output, error=None),
        )


class FakeClient:
    def __init__(self, tools):
        self.tools = tools


class ArcadeReadyOpsTest(unittest.TestCase):
    def test_discovers_exactly_one_ready_tool(self):
        client = FakeClient(FakeTools(names=['Other.Tool', 'SessionSearchMaterializer.MarkBundleReady']))
        self.assertEqual(discover_mark_bundle_ready(client), 'SessionSearchMaterializer.MarkBundleReady')

    def test_discovers_ready_tool_from_hosted_server_scope(self):
        tools = FakeTools(
            names=[],
            scoped_names={'session_search_materializer': ['SessionSearchMaterializer.MarkBundleReady']},
        )
        client = FakeClient(tools)
        self.assertEqual(discover_mark_bundle_ready(client), 'SessionSearchMaterializer.MarkBundleReady')
        self.assertEqual(tools.list_calls[0]['toolkit'], 'session_search_materializer')

    def test_ambiguous_ready_tool_discovery_fails_closed(self):
        client = FakeClient(FakeTools(names=['A.MarkBundleReady', 'B.mark_bundle_ready']))
        with self.assertRaisesRegex(ReadyOpsError, 'MARK_BUNDLE_READY_DISCOVERY_FAILED'):
            discover_mark_bundle_ready(client)

    def test_executes_exact_ready_contract_without_activation_surface(self):
        generation = 'a' * 64
        manifest = 'b' * 64
        output = {
            'status': 'READY',
            'target_state': 'READY',
            'generation_id': generation,
            'source_manifest_sha256': manifest,
            'session_count': 60,
            'message_count': 17790,
            'neon_project_id': 'wild-surf-61625738',
            'neon_branch_id': 'br-floral-morning-b5kactgx',
            'neon_database_name': 'neondb',
            'ready_role': 'session_search_arcade_ready_sql',
        }
        tools = FakeTools(names=['SessionSearchMaterializer.MarkBundleReady'], output=output)
        result = execute_mark_bundle_ready(
            FakeClient(tools), user_id='operator',
            neon_project_id='wild-surf-61625738', neon_branch_id='br-floral-morning-b5kactgx',
            generation_id=generation, expected_source_manifest_sha256=manifest,
            expected_session_count=60, expected_message_count=17790,
            neon_database_name='neondb',
        )
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['execution_id'], 'tc_ready_1')
        sent = tools.executed[1]
        self.assertNotIn('activate_generation', sent)
        self.assertNotIn('activate', sent)
        self.assertEqual(sent['generation_id'], generation)

    def test_unexpected_oauth_requirement_fails_without_manual_flow(self):
        tools = FakeTools(names=['SessionSearchMaterializer.MarkBundleReady'], auth_status='pending')
        with self.assertRaisesRegex(ReadyOpsError, 'READY_TOOL_UNEXPECTED_AUTH_REQUIREMENT'):
            execute_mark_bundle_ready(
                FakeClient(tools), user_id='operator',
                neon_project_id='wild-surf-61625738', neon_branch_id='br-floral-morning-b5kactgx',
                generation_id='a' * 64, expected_source_manifest_sha256='b' * 64,
                expected_session_count=60, expected_message_count=17790,
                neon_database_name='neondb',
            )


if __name__ == '__main__':
    unittest.main()
