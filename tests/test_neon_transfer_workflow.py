import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / '.github/workflows/transfer-session-search-neon-project.yml'


class NeonTransferWorkflowTest(unittest.TestCase):
    def test_is_manual_only_and_uses_secret_without_echoing_it(self):
        text = WF.read_text(encoding='utf-8')
        self.assertIn('workflow_dispatch:', text)
        self.assertNotIn('pull_request:', text)
        self.assertNotIn('push:', text)
        self.assertIn('NEON_API_KEY: ${{ secrets.NEON_API_KEY }}', text)
        self.assertNotIn('echo $NEON_API_KEY', text)
        self.assertNotIn('print(api_key)', text)

    def test_ambiguous_post_response_read_failure_is_reconciled(self):
        text = WF.read_text(encoding='utf-8')
        self.assertIn('import http.client', text)
        self.assertIn('http.client.HTTPException', text)
        self.assertIn('transfer_error = type(exc).__name__', text)
        self.assertIn('after = project_readback()', text)

    def test_fail_closed_exact_transfer_contract(self):
        text = WF.read_text(encoding='utf-8')
        for token in (
            'PROJECT_SOURCE_ORG_MISMATCH',
            'PROJECT_NAME_READBACK_MISMATCH',
            "'destination_org_id': destination_org",
            "'project_ids': [project_id]",
            'TRANSFER_POSTCONDITION_FAILED',
            'TRANSFER_OUTCOME_UNKNOWN',
            'NEON_PROJECT_TRANSFER_PASS',
        ):
            self.assertIn(token, text)
        self.assertIn('cancel-in-progress: false', text)


if __name__ == '__main__':
    unittest.main()
