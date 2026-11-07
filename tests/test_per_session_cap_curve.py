import importlib.util
import pathlib
import sqlite3
import tempfile
import unittest

from session_search.corpus_store import ingest_artifact
from session_search.search import _recall_candidate_rows
from tests.test_corpus_store import _message, _write_capture

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'experiments' / '013-per-session-cap-curve' / 'benchmark.py'


def load():
    spec = importlib.util.spec_from_file_location('cap_curve', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PerSessionCapCurveTest(unittest.TestCase):
    def _corpus(self, root: pathlib.Path) -> pathlib.Path:
        corpus = root / 'corpus'
        fixtures = {
            'a': ['needle alpha one', 'needle alpha two', 'needle alpha three', 'needle alpha four'],
            'b': ['needle beta one', 'needle beta two', 'needle beta three'],
            'c': ['needle gamma one'],
        }
        for idx, (sid, texts) in enumerate(fixtures.items()):
            capture = _write_capture(
                root / f'{idx}.zip',
                sid,
                [_message(f'{sid}{i}', text, float(i), role='assistant') for i, text in enumerate(texts, 1)],
                complete=True,
                title=sid,
            )
            ingest_artifact(capture, corpus)
        return corpus

    def test_complete_domain_is_one_through_budget(self):
        b = load()
        self.assertEqual(b.cap_domain(5), [1,2,3,4,5])
        with self.assertRaisesRegex(ValueError, 'BUDGET_INVALID'):
            b.cap_domain(0)

    def test_simulated_cap_matches_direct_candidate_query(self):
        b = load()
        with tempfile.TemporaryDirectory() as td:
            corpus = self._corpus(pathlib.Path(td))
            conn = sqlite3.connect(corpus / 'corpus.sqlite3')
            conn.row_factory = sqlite3.Row
            evidence = b.raw_evidence(conn, query='needle', scopes=['dialogue','evidence'], max_per_session=6)
            for cap in (1,2,3,4,5):
                actual = b.apply_policy(evidence, query='needle', per_session_cap=cap, candidate_budget=6)
                expected = b.direct_candidate_rows(conn, query='needle', scopes=['dialogue','evidence'], per_session_cap=cap, candidate_budget=6)
                self.assertEqual(
                    [(r['session_id'],r['ordinal'],r['message_id']) for r in actual],
                    [(r['session_id'],r['ordinal'],r['message_id']) for r in expected],
                    cap,
                )

    def test_current_point_matches_production_helper(self):
        b = load()
        with tempfile.TemporaryDirectory() as td:
            corpus = self._corpus(pathlib.Path(td))
            conn = sqlite3.connect(corpus / 'corpus.sqlite3')
            conn.row_factory = sqlite3.Row
            evidence = b.raw_evidence(conn, query='needle', scopes=['dialogue','evidence'], max_per_session=30)
            actual = b.apply_policy(evidence, query='needle', per_session_cap=3, candidate_budget=30)
            expected = _recall_candidate_rows(conn, 'needle', ['dialogue','evidence'], 10, None)
            self.assertEqual(
                [(r['session_id'],r['ordinal'],r['message_id']) for r in actual],
                [(r['session_id'],r['ordinal'],r['message_id']) for r in expected],
            )

    def test_selection_rule_excludes_topk_losses_then_uses_smallest_saturated_cap(self):
        b = load()
        points = [
            {'per_session_cap': 3, 'r_at_k': 0.35, 'candidate_recall': 0.55, 'vs_current': {'topk_lost': 0}},
            {'per_session_cap': 5, 'r_at_k': 0.40, 'candidate_recall': 0.60, 'vs_current': {'topk_lost': 0}},
            {'per_session_cap': 7, 'r_at_k': 0.40, 'candidate_recall': 0.65, 'vs_current': {'topk_lost': 0}},
            {'per_session_cap': 8, 'r_at_k': 0.45, 'candidate_recall': 0.70, 'vs_current': {'topk_lost': 1}},
            {'per_session_cap': 9, 'r_at_k': 0.40, 'candidate_recall': 0.65, 'vs_current': {'topk_lost': 0}},
        ]
        self.assertEqual(b.select_challenger(points)['selected_cap'], 7)

    def test_concentration_metrics_measure_candidate_monopoly(self):
        b = load()
        rows=[
            {'session_id':'a'}, {'session_id':'a'}, {'session_id':'a'},
            {'session_id':'b'}, {'session_id':'c'},
        ]
        self.assertEqual(b.concentration(rows), {'candidate_count':5,'unique_sessions':3,'max_session_share':0.6})


if __name__ == '__main__':
    unittest.main()
