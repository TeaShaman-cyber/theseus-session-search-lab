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

    def test_workflow_has_fail_closed_alternative_review_fallback(self):
        workflow = (ROOT / '.github/workflows/review-gate.yml').read_text()
        self.assertIn('tools/pr/alternative-review-status.sh', workflow)
        self.assertIn('UNAVAILABLE', workflow)
        self.assertIn('Codex review has unresolved findings', workflow)
        self.assertIn('Alternative exact-head review completed clean', workflow)
        self.assertIn('fallback_status', workflow)

    def test_alternative_review_wrapper_requests_all_comment_pages(self):
        wrapper = (ROOT / 'tools/pr/alternative-review-status.sh').read_text()
        self.assertIn('--paginate', wrapper)
        self.assertIn('--slurp', wrapper)
        self.assertIn('alternative_review_status.py', wrapper)

    def test_review_job_uses_the_same_head_classified_by_first_job(self):
        workflow = (ROOT / '.github/workflows/review-gate.yml').read_text()
        self.assertIn('steps.classify.outputs.head_sha', workflow)
        self.assertIn('echo "head_sha=$HEAD_SHA" >> "$GITHUB_OUTPUT"', workflow)
        self.assertIn('needs.classify.outputs.head_sha', workflow)

    def test_posthog_review_receipt_is_advisory_and_scoped(self):
        qa = (ROOT / 'docs/qa.md').read_text()
        self.assertIn('POSTHOG_CI_HISTORY:', qa)
        self.assertIn('sessionsearch', qa)
        self.assertIn('649512', qa)
        self.assertIn('not a merge authority', qa)


if __name__ == '__main__':
    unittest.main()
