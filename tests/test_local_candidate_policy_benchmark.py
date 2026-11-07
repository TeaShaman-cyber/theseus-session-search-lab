import importlib.util
import pathlib
import sqlite3
import tempfile
import unittest

from session_search.corpus_store import ingest_artifact
from session_search.search import _recall_candidate_rows
from tests.test_corpus_store import _message, _write_capture

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'experiments' / '011-local-candidate-policy' / 'benchmark.py'


def load():
    spec = importlib.util.spec_from_file_location('local_candidate_policy', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LocalCandidatePolicyBenchmarkTest(unittest.TestCase):
    def _corpus(self, root: pathlib.Path) -> pathlib.Path:
        corpus = root / 'corpus'
        for idx, (sid, texts) in enumerate({
            'a': ['needle alpha one', 'needle alpha two', 'needle alpha three', 'needle alpha four'],
            'b': ['needle beta one', 'needle beta two'],
            'c': ['needle gamma one'],
        }.items()):
            capture = _write_capture(
                root / f'{idx}.zip',
                sid,
                [_message(f'{sid}{i}', text, float(i), role='assistant') for i, text in enumerate(texts, 1)],
                complete=True,
                title=sid,
            )
            ingest_artifact(capture, corpus)
        return corpus

    def test_current_policy_matches_production_candidate_helper(self):
        b = load()
        with tempfile.TemporaryDirectory() as td:
            corpus = self._corpus(pathlib.Path(td))
            conn = sqlite3.connect(corpus / 'corpus.sqlite3')
            conn.row_factory = sqlite3.Row
            expected = _recall_candidate_rows(conn, 'needle', ['dialogue', 'evidence'], 10, None)
            actual = b.candidate_rows(
                conn,
                query='needle',
                scopes=['dialogue', 'evidence'],
                per_session_cap=3,
                candidate_budget=30,
            )
            self.assertEqual(
                [(r['session_id'], r['ordinal'], r['message_id']) for r in actual],
                [(r['session_id'], r['ordinal'], r['message_id']) for r in expected],
            )

    def test_higher_per_session_cap_can_expose_more_rows_without_changing_query_mode(self):
        b = load()
        with tempfile.TemporaryDirectory() as td:
            corpus = self._corpus(pathlib.Path(td))
            conn = sqlite3.connect(corpus / 'corpus.sqlite3')
            conn.row_factory = sqlite3.Row
            low = b.candidate_rows(conn, query='needle', scopes=['dialogue', 'evidence'], per_session_cap=1, candidate_budget=30)
            high = b.candidate_rows(conn, query='needle', scopes=['dialogue', 'evidence'], per_session_cap=5, candidate_budget=30)
            self.assertLess(len(low), len(high))
            self.assertEqual({r['session_id'] for r in low}, {'a', 'b', 'c'})
            self.assertEqual({r['session_id'] for r in high}, {'a', 'b', 'c'})

    def test_public_policy_result_does_not_expose_private_query_material(self):
        b = load()
        case = b.public_case(
            {"case_id": "c1", "query_lang": "ru", "query_text": "private", "target_content_fp": "secret"},
            rank=4,
        )
        self.assertEqual(case, {"case_id": "c1", "query_lang": "ru", "target_rank": 4})
        self.assertNotIn("query_text", case)
        self.assertNotIn("target_content_fp", case)

    def test_compare_cases_reports_reachability_and_topk_moves(self):
        b = load()
        baseline = [
            {"case_id": "a", "query_lang": "ru", "target_rank": None},
            {"case_id": "b", "query_lang": "ru", "target_rank": 12},
            {"case_id": "c", "query_lang": "ru", "target_rank": 3},
        ]
        candidate = [
            {"case_id": "a", "query_lang": "ru", "target_rank": 7},
            {"case_id": "b", "query_lang": "ru", "target_rank": 8},
            {"case_id": "c", "query_lang": "ru", "target_rank": 4},
        ]
        self.assertEqual(
            b.compare_cases(baseline, candidate, k=10),
            {
                "reach_gained": 1, "reach_lost": 0,
                "topk_gained": 2, "topk_lost": 0,
                "rank_improved": 2, "rank_same": 0, "rank_worse": 1,
            },
        )

    def test_invalid_policy_fails_closed(self):
        b = load()
        with self.assertRaisesRegex(ValueError, 'PER_SESSION_CAP_INVALID'):
            b.validate_policy(0, 30)
        with self.assertRaisesRegex(ValueError, 'CANDIDATE_BUDGET_INVALID'):
            b.validate_policy(3, 0)


if __name__ == '__main__':
    unittest.main()
