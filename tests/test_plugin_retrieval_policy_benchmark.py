import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "experiments" / "006-plugin-retrieval-policy-usability" / "benchmark.py"


def _load_benchmark():
    spec = importlib.util.spec_from_file_location("plugin_retrieval_policy_benchmark", BENCHMARK)
    if spec is None or spec.loader is None:
        raise RuntimeError("benchmark import failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PluginRetrievalPolicyBenchmarkTest(unittest.TestCase):
    def test_frozen_policy_benchmark_covers_required_usability_classes(self):
        benchmark = _load_benchmark()
        receipt = benchmark.run_benchmark()
        self.assertEqual(receipt["overall"], "PASS")
        self.assertEqual(receipt["case_count"], 4)
        self.assertEqual(receipt["pass_count"], 4)
        self.assertEqual(
            {case["class"] for case in receipt["cases"]},
            {
                "PRECISE_LOOKUP",
                "OVERSPECIFIED_NATURAL_LANGUAGE",
                "SESSION_LEVEL_CONCEPT_SPLIT",
                "CLEAN_MISS_UNKNOWN",
            },
        )
        for case in receipt["cases"]:
            self.assertTrue(case["checks"]["plugin_strict_matches_cli_strict"])
            self.assertTrue(case["checks"]["plugin_recall_matches_cli_recall"])
            self.assertTrue(case["checks"]["auto_matches_bounded_policy"])
            self.assertTrue(case["checks"]["receipt_matches_policy"])
            self.assertEqual(case["plugin"]["unavailable_shards"], [])

    def test_committed_public_receipt_matches_current_benchmark(self):
        benchmark = _load_benchmark()
        expected = json.loads(
            (BENCHMARK.parent / "baseline.public.json").read_text(encoding="utf-8")
        )
        self.assertEqual(benchmark.run_benchmark(), expected)


if __name__ == "__main__":
    unittest.main()
