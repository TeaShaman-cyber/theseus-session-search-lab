import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SELECTOR_PATH = ROOT / "experiments" / "018-selective-sparse-view" / "selector.py"


def load_selector():
    spec = importlib.util.spec_from_file_location("selective_sparse_h3", SELECTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SelectiveSparseH3Test(unittest.TestCase):
    def test_half_glue_routes_to_sparse(self):
        selector = load_selector()
        features = [
            {"token": "вспомнить", "kind": "glue"},
            {"token": "сообщение", "kind": "glue"},
            {"token": "grafana", "kind": "technical"},
            {"token": "дашборд", "kind": "nominal"},
        ]
        decision = selector.select_view(features)
        self.assertEqual(decision["mode"], "sparse")
        self.assertEqual(decision["glue_ratio"], 0.5)
        self.assertEqual(decision["unique_tokens"], 4)

    def test_below_half_glue_keeps_full_query(self):
        selector = load_selector()
        features = [
            {"token": "вспомнить", "kind": "glue"},
            {"token": "grafana", "kind": "technical"},
            {"token": "дашборд", "kind": "nominal"},
        ]
        decision = selector.select_view(features)
        self.assertEqual(decision["mode"], "full")
        self.assertAlmostEqual(decision["glue_ratio"], 1 / 3)

    def test_duplicate_normalized_token_counts_once(self):
        selector = load_selector()
        features = [
            {"token": "найти", "kind": "glue"},
            {"token": "найти", "kind": "glue"},
            {"token": "tempo", "kind": "technical"},
        ]
        decision = selector.select_view(features)
        self.assertEqual(decision["unique_tokens"], 2)
        self.assertEqual(decision["glue_tokens"], 1)
        self.assertEqual(decision["mode"], "sparse")

    def test_first_kind_for_duplicate_token_is_authoritative(self):
        selector = load_selector()
        features = [
            {"token": "chat", "kind": "glue"},
            {"token": "chat", "kind": "technical"},
            {"token": "tempo", "kind": "technical"},
        ]
        decision = selector.select_view(features)
        self.assertEqual(decision["glue_tokens"], 1)
        self.assertEqual(decision["mode"], "sparse")

    def test_empty_query_stays_full(self):
        selector = load_selector()
        decision = selector.select_view([])
        self.assertEqual(
            decision,
            {
                "mode": "full",
                "glue_ratio": 0.0,
                "glue_tokens": 0,
                "unique_tokens": 0,
            },
        )

    def test_invalid_feature_fails_closed(self):
        selector = load_selector()
        with self.assertRaisesRegex(ValueError, "SELECTOR_FEATURE_INVALID"):
            selector.select_view([{"token": "", "kind": "glue"}])


if __name__ == "__main__":
    unittest.main()
