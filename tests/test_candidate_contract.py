import json
import pathlib
import unittest

from session_search.candidate_contract import project_candidate_v1, project_local_candidate_v1

ROOT = pathlib.Path(__file__).resolve().parents[1]


class CandidateContractTest(unittest.TestCase):
    def test_python_matches_shared_local_and_serverless_fixture(self):
        fixture = json.loads((ROOT / 'tests' / 'fixtures' / 'candidate_v1.json').read_text())
        for case in fixture.values():
            self.assertEqual(
                project_candidate_v1(case['input'], score_order=case['score_order']),
                case['expected'],
            )

    def test_local_adapter_binds_sqlite_score_orientation(self):
        fixture = json.loads((ROOT / 'tests' / 'fixtures' / 'candidate_v1.json').read_text())
        self.assertEqual(project_local_candidate_v1(fixture['local']['input']), fixture['local']['expected'])

    def test_rejects_ambiguous_score_orientation(self):
        row = {'session_id':'s','ordinal':0,'text':'x','search_class':'dialogue','score':1.0}
        with self.assertRaisesRegex(ValueError, 'CANDIDATE_SCORE_ORDER_INVALID'):
            project_candidate_v1(row, score_order='unknown')


if __name__ == '__main__':
    unittest.main()
