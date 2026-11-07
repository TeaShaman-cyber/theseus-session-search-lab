import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "008-real-corpus-exact-grouping" / "benchmark.py"

def load():
    spec = importlib.util.spec_from_file_location("real_corpus_exact_grouping", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module

class ExactGroupingBenchmark(unittest.TestCase):
    def setUp(self):
        self.b = load()
        self.rows = [
            {"session_id":"a","ordinal":1,"message_id":"x","text":"same","score":-9.0,"search_class":"dialogue"},
            {"session_id":"b","ordinal":1,"message_id":"x","text":"same","score":-8.0,"search_class":"dialogue"},
            {"session_id":"c","ordinal":1,"message_id":"x","text":"target","score":-7.0,"search_class":"dialogue"},
            {"session_id":"d","ordinal":1,"message_id":"x","text":"other","score":-6.0,"search_class":"dialogue"},
        ]

    def test_grouping_preserves_all_candidate_occurrences(self):
        groups = self.b.group_exact_text(self.rows)
        self.assertEqual([g["text"] for g in groups], ["same", "target", "other"])
        self.assertEqual(groups[0]["occurrences"], [
            ["a","x",1], ["b","x",1],
        ])
        self.assertEqual(sum(len(g["occurrences"]) for g in groups), len(self.rows))

    def test_same_message_id_different_body_remains_distinct(self):
        groups = self.b.group_exact_text(self.rows)
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[1]["occurrences"], [["c","x",1]])

    def test_compare_topk_uses_same_ordered_candidate_pool(self):
        result = self.b.compare_topk(self.rows, target_text="target", k=3)
        self.assertEqual(result["baseline_target_rank"], 3)
        self.assertEqual(result["grouped_target_rank"], 2)
        self.assertEqual(result["baseline_unique_texts"], 2)
        self.assertEqual(result["grouped_unique_texts"], 3)

    def test_empty_and_bad_k(self):
        self.assertEqual(self.b.group_exact_text([]), [])
        with self.assertRaises(ValueError):
            self.b.compare_topk([], target_text="x", k=0)

if __name__ == "__main__":
    unittest.main()
