import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'experiments' / '009-mmr-diversity' / 'benchmark.py'


def load():
    spec = importlib.util.spec_from_file_location('mmr_diversity', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MMRDiversityTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.groups = [
            {'text': 'alpha beta', 'representative': {'score': 999}},
            {'text': 'alpha beta gamma', 'representative': {'score': -999}},
            {'text': 'delta epsilon', 'representative': {'score': 0}},
        ]

    def test_lambda_one_preserves_rank_order(self):
        chosen = self.m.mmr_select(self.groups, k=2, lambda_relevance=1.0)
        self.assertEqual([g['text'] for g in chosen], ['alpha beta', 'alpha beta gamma'])

    def test_lambda_zero_prefers_dissimilar_second_result(self):
        chosen = self.m.mmr_select(self.groups, k=2, lambda_relevance=0.0)
        self.assertEqual([g['text'] for g in chosen], ['alpha beta', 'delta epsilon'])

    def test_selection_uses_rank_order_not_backend_score_scale(self):
        changed = [dict(g, representative={'score': i * 1_000_000}) for i, g in enumerate(self.groups)]
        self.assertEqual(
            [g['text'] for g in self.m.mmr_select(changed, k=2, lambda_relevance=0.2)],
            [g['text'] for g in self.m.mmr_select(self.groups, k=2, lambda_relevance=0.2)],
        )

    def test_invalid_lambda_or_k_fails_closed(self):
        with self.assertRaises(ValueError):
            self.m.mmr_select(self.groups, k=2, lambda_relevance=-0.1)
        with self.assertRaises(ValueError):
            self.m.mmr_select(self.groups, k=0, lambda_relevance=0.5)


if __name__ == '__main__':
    unittest.main()
