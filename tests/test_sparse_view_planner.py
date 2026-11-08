import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLANNER_PATH = ROOT / "experiments" / "016-sparse-view-planner" / "planner.py"


def load_planner():
    spec = importlib.util.spec_from_file_location("sparse_view_planner", PLANNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SparseViewPlannerTest(unittest.TestCase):
    def test_token_classes_are_fixed_before_rank_evaluation(self):
        planner = load_planner()
        self.assertEqual(planner.classify_token("найти", "найти", pos="INFN"), "glue")
        self.assertEqual(planner.classify_token("сообщение", "сообщение", pos="NOUN"), "glue")
        self.assertEqual(planner.classify_token("the", "the"), "glue")
        self.assertEqual(planner.classify_token("messages", "messag"), "glue")
        self.assertEqual(planner.classify_token("grafana", "grafana"), "technical")
        self.assertEqual(planner.classify_token("issue53", "issue53"), "identifier")
        self.assertEqual(planner.classify_token("дашборд", "дашборд", pos="NOUN"), "nominal")
        self.assertEqual(planner.classify_token("старый", "старый", pos="ADJF"), "qualifier")
        self.assertEqual(planner.classify_token("про", "про", pos="PREP"), "glue")
        self.assertEqual(planner.classify_token("работать", "работать", pos="INFN"), "other")

    def test_prefers_identifiers_technical_and_nominal_terms_over_glue(self):
        planner = load_planner()
        features = [
            {"token": "найти", "ordinal": 0, "idf": 0.5, "kind": "glue"},
            {"token": "сообщение", "ordinal": 1, "idf": 0.7, "kind": "glue"},
            {"token": "grafana", "ordinal": 2, "idf": 7.0, "kind": "technical"},
            {"token": "дашборд", "ordinal": 3, "idf": 4.0, "kind": "nominal"},
            {"token": "issue53", "ordinal": 4, "idf": 8.0, "kind": "identifier"},
            {"token": "настройка", "ordinal": 5, "idf": 2.0, "kind": "nominal"},
        ]
        plan = planner.plan_features(features, budget=3)
        self.assertEqual(plan["tokens"], ["grafana", "дашборд", "issue53"])
        self.assertEqual(plan["fallback"], False)

    def test_idf_breaks_ties_inside_same_semantic_class(self):
        planner = load_planner()
        features = [
            {"token": "первый", "ordinal": 0, "idf": 2.0, "kind": "qualifier"},
            {"token": "старый", "ordinal": 1, "idf": 6.0, "kind": "qualifier"},
            {"token": "ранний", "ordinal": 2, "idf": 4.0, "kind": "qualifier"},
        ]
        plan = planner.plan_features(features, budget=2)
        self.assertEqual(plan["tokens"], ["старый", "ранний"])

    def test_selected_tokens_return_in_original_query_order(self):
        planner = load_planner()
        features = [
            {"token": "alpha", "ordinal": 0, "idf": 3.0, "kind": "technical"},
            {"token": "beta", "ordinal": 1, "idf": 9.0, "kind": "technical"},
            {"token": "gamma", "ordinal": 2, "idf": 8.0, "kind": "technical"},
        ]
        plan = planner.plan_features(features, budget=2)
        self.assertEqual(plan["tokens"], ["beta", "gamma"])

    def test_duplicate_normalized_tokens_do_not_consume_budget_twice(self):
        planner = load_planner()
        features = [
            {"token": "grafana", "ordinal": 0, "idf": 7.0, "kind": "technical"},
            {"token": "grafana", "ordinal": 2, "idf": 7.0, "kind": "technical"},
            {"token": "tempo", "ordinal": 3, "idf": 6.0, "kind": "technical"},
        ]
        plan = planner.plan_features(features, budget=2)
        self.assertEqual(plan["tokens"], ["grafana", "tempo"])

    def test_all_glue_falls_back_to_original_unique_tokens(self):
        planner = load_planner()
        features = [
            {"token": "найти", "ordinal": 0, "idf": 0.5, "kind": "glue"},
            {"token": "сообщение", "ordinal": 1, "idf": 0.7, "kind": "glue"},
            {"token": "найти", "ordinal": 2, "idf": 0.5, "kind": "glue"},
        ]
        plan = planner.plan_features(features, budget=5)
        self.assertEqual(plan["tokens"], ["найти", "сообщение"])
        self.assertEqual(plan["fallback"], True)

    def test_budget_is_bounded(self):
        planner = load_planner()
        features = [
            {"token": f"term{i}", "ordinal": i, "idf": 10 - i, "kind": "technical"}
            for i in range(8)
        ]
        plan = planner.plan_features(features, budget=5)
        self.assertEqual(len(plan["tokens"]), 5)

    def test_invalid_budget_fails_closed(self):
        planner = load_planner()
        with self.assertRaisesRegex(ValueError, "QUERY_PLANNER_BUDGET_INVALID"):
            planner.plan_features([], budget=0)


if __name__ == "__main__":
    unittest.main()
