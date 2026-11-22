import unittest

from scripts.review_gate_required import classify_paths


class ReviewGateRequiredTests(unittest.TestCase):
    def test_protected_paths_require_review(self):
        cases = [
            '.github/workflows/promote-session-search-ready.yml',
            'tools/pr/codex-review-wait.sh',
            'scripts/codex_review_status.py',
            'scripts/verify_repo.py',
            'scripts/arcade_activate_generation.py',
            'scripts/review_gate_required.py',
            'tools/dev/check',
            'docs/qa.md',
            'functions/router/core.mjs',
            'functions/auth.mjs',
            'sql/neon/002_federated_shard_privileges.sql',
            'sql/neon/006_activation_marker_capability.sql',
            'functions/shard/handler.mjs',
            'functions/candidate-contract.mjs',
            'session_search/corpus_store.py',
            'session_search/reconciliation.py',
            'session_search/neon_materializer.py',
            'session_search/refresh_plan.py',
            'scripts/materialize_neon_shard.py',
        ]
        for path in cases:
            with self.subTest(path=path):
                result = classify_paths([path])
                self.assertTrue(result['required'])
                self.assertEqual(result['matches'][0]['path'], path)

    def test_canonical_spec_d_artifacts_require_review(self):
        cases = [
            'CAPABILITY_MAP-gemini.md',
            'CAPABILITY_MAP.md',
            'SPEC-gemini-source-acceptance.md',
            'SPEC-gemini-serving-bootstrap.md',
            'tasks/plan.md',
            'tasks/todo.md',
        ]
        for path in cases:
            with self.subTest(path=path):
                result = classify_paths([path])
                self.assertTrue(result['required'])
                self.assertEqual(result['matches'][0]['path'], path)

    def test_spec_d_patterns_do_not_match_noncanonical_paths(self):
        cases = [
            'docs/SPEC-gemini.md',
            'specs/CAPABILITY_MAP.md',
            'capability_map.md',
            'spec-gemini.md',
            'CAPABILITY_MAP.txt',
            'SPEC.md',
            'CAPABILITY_MAP.md.bak',
        ]
        for path in cases:
            with self.subTest(path=path):
                result = classify_paths([path])
                self.assertFalse(result['required'])
                self.assertEqual(result['matches'], [])

    def test_ordinary_paths_do_not_require_review(self):
        result = classify_paths([
            'README.md',
            'docs/retrieval-feedback.md',
            'tests/test_search.py',
            'session_search/search.py',
            'functions/tests/serving.test.mjs',
        ])
        self.assertFalse(result['required'])
        self.assertEqual(result['matches'], [])

    def test_any_protected_path_makes_mixed_diff_require_review(self):
        result = classify_paths(['README.md', 'tools/pr/codex-review-status.sh'])
        self.assertTrue(result['required'])
        self.assertEqual(len(result['matches']), 1)

    def test_paths_are_normalized_and_duplicates_are_collapsed(self):
        result = classify_paths([
            r'tools\\pr\\codex-review-status.sh',
            'tools/pr/codex-review-status.sh',
        ])
        self.assertTrue(result['required'])
        self.assertEqual(len(result['matches']), 1)

    def test_empty_diff_does_not_require_review(self):
        self.assertEqual(classify_paths([]), {'required': False, 'matches': []})


if __name__ == '__main__':
    unittest.main()
