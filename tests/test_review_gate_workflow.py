import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ReviewGateWorkflowContractTests(unittest.TestCase):
    def test_workflow_uses_existing_exact_head_review_helpers(self):
        workflow = (ROOT / '.github/workflows/review-gate.yml').read_text()
        self.assertIn('scripts/review_gate_required.py', workflow)
        self.assertIn('tools/pr/codex-review-wait.sh', workflow)
        self.assertIn('github.event.pull_request.head.sha', workflow)
        self.assertIn('github.event.pull_request.base.sha', workflow)
        self.assertIn('FINDINGS', workflow)
        self.assertIn('persist-credentials: false', workflow)
        self.assertIn('git diff --no-renames --name-only', workflow)

    def test_posthog_review_receipt_is_advisory_and_scoped(self):
        qa = (ROOT / 'docs/qa.md').read_text()
        self.assertIn('POSTHOG_CI_HISTORY:', qa)
        self.assertIn('sessionsearch', qa)
        self.assertIn('649512', qa)
        self.assertIn('not a merge authority', qa)


if __name__ == '__main__':
    unittest.main()
