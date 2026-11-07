import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "experiments"
    / "005-translation-provider-selection"
    / "benchmark_core.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("translation_benchmark_core", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TranslationBenchmarkCoreTests(unittest.TestCase):
    def test_canonical_translation_payload_is_order_independent_and_explicit(self):
        mod = load_module()
        rows = [
            {
                "case_id": "b",
                "translated_ru": "два",
                "target_content_fp": "2" * 32,
            },
            {
                "case_id": "a",
                "translated_ru": "один",
                "target_content_fp": "1" * 32,
            },
        ]

        payload = mod.canonical_translation_payload(rows)

        expected = json.dumps(
            sorted(rows, key=lambda row: row["case_id"]),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        self.assertEqual(payload, expected)
        self.assertEqual(
            mod.translation_digest(rows),
            hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        )


    def test_gold_digest_is_order_independent_and_covers_frozen_fields(self):
        mod = load_module()
        rows = [
            {
                "case_id": "b",
                "query_lang": "ru",
                "query_text": "два",
                "target_content_fp": "2" * 32,
            },
            {
                "case_id": "a",
                "query_lang": "en",
                "query_text": "one",
                "target_content_fp": "1" * 32,
            },
        ]

        payload = mod.canonical_gold_payload(rows)

        expected = json.dumps(
            sorted(rows, key=lambda row: (row["case_id"], row["query_lang"])),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        self.assertEqual(payload, expected)
        self.assertEqual(
            mod.gold_digest(rows),
            hashlib.sha256(expected.encode("utf-8")).hexdigest(),
        )

    def test_retrieval_metrics_treat_missing_rank_as_miss(self):
        mod = load_module()
        ranks = [1, 2, 5, 10, None]

        metrics = mod.retrieval_metrics(ranks)

        self.assertEqual(metrics["count"], 5)
        self.assertEqual(metrics["r_at_1"], 0.2)
        self.assertEqual(metrics["r_at_3"], 0.4)
        self.assertEqual(metrics["r_at_5"], 0.6)
        self.assertEqual(metrics["r_at_10"], 0.8)
        self.assertAlmostEqual(metrics["mrr"], (1 + 1 / 2 + 1 / 5 + 1 / 10) / 5)

    def test_latency_summary_uses_inclusive_linear_percentiles(self):
        mod = load_module()
        samples = [10.0, 20.0, 30.0, 40.0]

        summary = mod.latency_summary(samples)

        self.assertEqual(summary["count"], 4)
        self.assertEqual(summary["p50_ms"], 25.0)
        self.assertAlmostEqual(summary["p95_ms"], 38.5)
        self.assertEqual(summary["mean_ms"], 25.0)

    def test_character_count_counts_exact_source_strings(self):
        mod = load_module()
        rows = [
            {"query_text": "abc"},
            {"query_text": "привет"},
            {"query_text": "x y"},
        ]

        self.assertEqual(mod.character_count(rows), 3 + 6 + 3)


if __name__ == "__main__":
    unittest.main()
