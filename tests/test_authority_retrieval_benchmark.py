import importlib.util
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "experiments" / "003-authority-retrieval-benchmark" / "benchmark.py"


def _load_benchmark():
    spec = importlib.util.spec_from_file_location("authority_retrieval_benchmark", BENCHMARK)
    if spec is None or spec.loader is None:
        raise RuntimeError("benchmark import failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AuthorityRetrievalBenchmarkTest(unittest.TestCase):
    def test_frozen_baseline_matches_preregistered_behavior(self):
        benchmark = _load_benchmark()
        receipt = benchmark.run_benchmark()
        self.assertEqual(receipt["overall"], "PASS")
        self.assertEqual(receipt["case_count"], 7)
        self.assertEqual(receipt["pass_count"], 7)

        cases = {case["id"]: case for case in receipt["cases"]}
        self.assertEqual(
            cases["alias-canonicalization-gap"]["baseline_disposition"],
            "GAP_CANDIDATE_FOR_RECONCILIATION",
        )
        self.assertEqual(
            cases["broad-context-candidate"]["baseline_disposition"],
            "RISK_IF_DOWNSTREAM_PROMOTES_RECALL",
        )
        self.assertEqual(
            cases["unresolved-ambiguity"]["baseline_disposition"],
            "GAP_NEEDS_EXPLICIT_AMBIGUITY_STATE",
        )

    def test_committed_baseline_receipt_matches_current_frozen_benchmark(self):
        benchmark = _load_benchmark()
        expected = json.loads(
            (BENCHMARK.parent / "baseline.public.json").read_text(encoding="utf-8")
        )
        self.assertEqual(benchmark.run_benchmark(), expected)


if __name__ == "__main__":
    unittest.main()
