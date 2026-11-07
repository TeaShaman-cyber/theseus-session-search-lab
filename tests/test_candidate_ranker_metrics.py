import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'experiments' / '010-candidate-ranker-separation' / 'metrics.py'

def load():
    spec = importlib.util.spec_from_file_location('candidate_ranker_metrics', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module

class CandidateRankerMetricsTest(unittest.TestCase):
    def test_separates_candidate_recall_from_ranker_topk(self):
        m = load()
        result = m.summarize_cases([
            {'candidate_reachable': True, 'ranked_rank': 2},
            {'candidate_reachable': False, 'ranked_rank': None},
            {'candidate_reachable': True, 'ranked_rank': 12},
        ], k=10)
        self.assertEqual(result['count'], 3)
        self.assertAlmostEqual(result['candidate_recall'], 2/3)
        self.assertAlmostEqual(result['r_at_k'], 1/3)
        self.assertAlmostEqual(result['reachable_to_topk'], 1/2)
        self.assertAlmostEqual(result['mrr'], (1/2 + 1/12)/3)

    def test_unreachable_candidate_cannot_have_rank(self):
        m = load()
        with self.assertRaisesRegex(ValueError, 'UNREACHABLE_HAS_RANK'):
            m.summarize_cases([
                {'candidate_reachable': False, 'ranked_rank': 1},
            ], k=10)

    def test_rank_must_be_positive_integer(self):
        m = load()
        with self.assertRaisesRegex(ValueError, 'RANK_INVALID'):
            m.summarize_cases([
                {'candidate_reachable': True, 'ranked_rank': 0},
            ], k=10)

    def test_empty_cases_fail_closed(self):
        m = load()
        with self.assertRaisesRegex(ValueError, 'CASES_EMPTY'):
            m.summarize_cases([], k=10)

if __name__ == '__main__':
    unittest.main()
