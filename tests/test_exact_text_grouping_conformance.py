import json
import pathlib
import unittest

from session_search.search import group_exact_search_rows

ROOT = pathlib.Path(__file__).resolve().parents[1]


class ExactTextGroupingConformanceTest(unittest.TestCase):
    def test_python_matches_shared_v1_fixture(self):
        fixture = json.loads((ROOT / 'tests' / 'fixtures' / 'exact_text_grouping_v1.json').read_text())
        groups = group_exact_search_rows(fixture['hits'])
        self.assertEqual(groups, fixture['expected'])
        self.assertEqual(
            sum(group['occurrence_count'] for group in groups),
            len(fixture['hits']),
        )


if __name__ == '__main__':
    unittest.main()
