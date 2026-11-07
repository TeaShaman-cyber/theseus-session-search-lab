import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'experiments' / '012-independent-holdout' / 'discover.py'


def load():
    spec = importlib.util.spec_from_file_location('holdout_discovery', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HoldoutDiscoveryTest(unittest.TestCase):
    def test_fixed_retrieval_attractors(self):
        d = load()
        positives = [
            'Друг, вспомни наш разговор про графы',
            'восстанови предыдущую ветку',
            'найди по нашим разговорам старый план',
            'проверь через Session Search, что мы обсуждали',
            'please recall our earlier conversation about this',
            'recover the previous discussion',
            'find in our conversations the old benchmark',
        ]
        negatives = [
            'найди свежие новости в интернете',
            'обычный разговор без retrieval intent',
        ]
        for text in positives:
            self.assertTrue(d.is_historical_retrieval_candidate(text), text)
        for text in negatives:
            self.assertFalse(d.is_historical_retrieval_candidate(text), text)

    def test_public_summary_exposes_only_aggregate_discovery_evidence(self):
        d = load()
        rows = [
            {'session_id':'s','ordinal':4,'message_id':'m','create_time':1.5,'text':'private query'},
            {'session_id':'t','ordinal':5,'message_id':'n','create_time':2.5,'text':'another private query'},
        ]
        public = d.public_summary(rows)
        self.assertEqual(public['candidate_count'], 2)
        self.assertEqual(public['first_create_time'], 1.5)
        self.assertEqual(public['last_create_time'], 2.5)
        self.assertNotIn('candidates', public)
        self.assertNotIn('text', public)
        self.assertNotIn('session_id', public)


if __name__ == '__main__':
    unittest.main()
