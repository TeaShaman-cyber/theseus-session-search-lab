import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'scripts') not in sys.path:
    sys.path.insert(0, str(ROOT / 'scripts'))

from arcade_activate_generation import ActivationOpsError, discover_activation_tool, execute_activation


class FakeTools:
    def __init__(self, *, names=None, scoped_names=None, output=None, auth_status='completed', success=True):
        self.names = names or []
        self.scoped_names = scoped_names or {}
        self.output = output
        self.auth_status = auth_status
        self.success = success
        self.executed = None

    def list(self, **kwargs):
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
            id='te_activation_1',
            output=SimpleNamespace(value=self.output, error=None),
        )


class FakeClient:
    def __init__(self, tools): self.tools = tools


class ArcadeActivationOpsTest(unittest.TestCase):
    def test_discovers_activation_tool_from_hosted_server_scope(self):
        tools = FakeTools(scoped_names={
            'session_search_materializer': ['SessionSearchMaterializer.ActivateReadyGeneration']
        })
        self.assertEqual(
            discover_activation_tool(FakeClient(tools)),
            'SessionSearchMaterializer.ActivateReadyGeneration',
        )

    def test_partial_paginated_discovery_fails_closed(self):
        tools = FakeTools(names=[])
        calls = 0
        original_list = tools.list
        def paged_list(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SimpleNamespace(items=[SimpleNamespace(qualified_name='SessionSearchMaterializer.ActivateReadyGeneration')], next_offset='next')
            raise RuntimeError('page failure')
        tools.list = paged_list
        with self.assertRaisesRegex(ActivationOpsError, 'ACTIVATION_TOOL_DISCOVERY_INCOMPLETE'):
            discover_activation_tool(FakeClient(tools))

    def test_ambiguous_activation_tool_discovery_fails_closed(self):
        tools = FakeTools(names=['A.ActivateReadyGeneration', 'B.activate_ready_generation'])
        with self.assertRaisesRegex(ActivationOpsError, 'ACTIVATION_TOOL_DISCOVERY_FAILED'):
            discover_activation_tool(FakeClient(tools))

    def test_executes_exact_null_to_candidate_cas_contract(self):
        generation = 'a' * 64
        output = {
            'status': 'ACTIVATED',
            'generation_id': generation,
            'expected_previous_generation_id': None,
            'active_generation': generation,
            'neon_project_id': 'wild-surf-61625738',
            'neon_branch_id': 'br-floral-morning-b5kactgx',
            'neon_database_name': 'neondb',
            'activator_role': 'session_search_arcade_activator_sql',
        }
        tools = FakeTools(
            scoped_names={'session_search_materializer': ['SessionSearchMaterializer.ActivateReadyGeneration']},
            output=output,
        )
        result = execute_activation(
            FakeClient(tools), user_id='operator',
            neon_project_id='wild-surf-61625738',
            neon_branch_id='br-floral-morning-b5kactgx',
            generation_id=generation,
            expected_previous_generation_id='NULL',
            neon_database_name='neondb',
        )
        self.assertEqual(result['status'], 'ACTIVATED')
        self.assertEqual(result['execution_id'], 'te_activation_1')
        sent = tools.executed[1]
        self.assertEqual(sent['expected_previous_generation_id'], 'NULL')
        self.assertEqual(sent['generation_id'], generation)
        self.assertNotIn('catalog', sent)
        self.assertNotIn('cutover', sent)

    def test_unexpected_auth_requirement_fails_without_manual_flow(self):
        tools = FakeTools(
            scoped_names={'session_search_materializer': ['SessionSearchMaterializer.ActivateReadyGeneration']},
            auth_status='pending',
        )
        with self.assertRaisesRegex(ActivationOpsError, 'ACTIVATION_TOOL_UNEXPECTED_AUTH_REQUIREMENT'):
            execute_activation(
                FakeClient(tools), user_id='operator',
                neon_project_id='wild-surf-61625738',
                neon_branch_id='br-floral-morning-b5kactgx',
                generation_id='a' * 64,
                expected_previous_generation_id='NULL',
                neon_database_name='neondb',
            )


if __name__ == '__main__':
    unittest.main()
