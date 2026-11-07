import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "007-exact-topk-diversity" / "benchmark.py"


def load():
    spec = importlib.util.spec_from_file_location("exact_topk_diversity", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TopKExactDiversity(unittest.TestCase):
    def setUp(self):
        self.b = load()
        # Source occurrences, not deduplicated messages. Same message ID on
        # different branches may refer to different texts.
        self.events = [
            {"session": "a", "id": "x", "ordinal": 1, "text": "alpha", "score": 9, "relevant": True},
            {"session": "b", "id": "x", "ordinal": 1, "text": "alpha", "score": 9, "relevant": True},
            {"session": "c", "id": "x", "ordinal": 1, "text": "alpha", "score": 9, "relevant": True},
            {"session": "d", "id": "x", "ordinal": 1, "text": "beta", "score": 8, "relevant": True},
            {"session": "e", "id": "y", "ordinal": 2, "text": "gamma", "score": 7, "relevant": False},
        ]

    def test_avoids_exact_duplicate_topk_saturation_without_losing_branch_refs(self):
        result = self.b.evaluate(self.events, k=3)
        self.assertEqual(result["baseline"]["unique_texts"], 1)
        self.assertEqual(result["exact_grouped"]["unique_texts"], 3)
        self.assertEqual(result["baseline"]["unique_relevant_texts"], 1)
        self.assertEqual(result["exact_grouped"]["unique_relevant_texts"], 2)
        self.assertEqual(result["exact_grouped"]["selected"][0]["members"],
                         [["a", "x", 1], ["b", "x", 1], ["c", "x", 1]])
        self.assertEqual(result["exact_grouped"]["selected"][1]["text"], "beta")

    def test_same_message_id_different_text_never_collapses(self):
        result = self.b.evaluate(self.events, k=5)
        self.assertEqual(result["exact_grouped"]["unique_texts"], 3)
        self.assertEqual(result["exact_grouped"]["selected"][1]["members"], [["d", "x", 1]])
        self.assertEqual(result["total_events"], 5)

    def test_conflicting_gold_for_identical_text_fails_closed(self):
        changed = [dict(e) for e in self.events]
        changed[1]["relevant"] = False
        with self.assertRaisesRegex(ValueError, "relevance conflicts"):
            self.b.evaluate(changed, k=3)

    def test_deterministic_order_and_empty_input(self):
        baseline = self.b.evaluate(list(reversed(self.events)), k=3)
        self.assertEqual(baseline, self.b.evaluate(self.events, k=3))
        self.assertEqual(self.b.evaluate([], k=3)["exact_grouped"]["selected"], [])
        with self.assertRaises(ValueError):
            self.b.evaluate(self.events, k=0)


if __name__ == "__main__":
    unittest.main()
