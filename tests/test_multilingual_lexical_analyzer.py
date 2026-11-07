import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "014-multilingual-lexical-seam" / "analyzer.py"


def load():
    spec = importlib.util.spec_from_file_location("multilingual_lexical_analyzer", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MultilingualLexicalAnalyzerTest(unittest.TestCase):
    def test_surface_normalizes_case_unicode_and_keeps_mixed_language_tokens(self):
        m = load()
        self.assertEqual(
            m.surface_tokens("GitHub MCP и ВОССТАНОВЛЕНИЕ корпуса"),
            ["github", "mcp", "и", "восстановление", "корпуса"],
        )

    def test_analyze_text_preserves_term_frequency_and_uses_supplied_token_normalizer(self):
        m = load()
        seen = []
        def normalize(token):
            seen.append(token)
            return {"корпуса": "корпус", "корпусом": "корпус"}.get(token, token)
        self.assertEqual(
            m.analyze_text("Корпуса корпусом MCP", normalize_token=normalize),
            "корпус корпус mcp",
        )
        self.assertEqual(seen, ["корпуса", "корпусом", "mcp"])

    def test_match_query_is_or_over_analyzed_tokens_and_quotes_safely(self):
        m = load()
        self.assertEqual(
            m.fts_or_query(["восстановить", "github", 'a"b']),
            '"восстановить" OR "github" OR "a""b"',
        )

    def test_empty_analysis_fails_closed(self):
        m = load()
        with self.assertRaisesRegex(ValueError, "LEXICAL_QUERY_EMPTY"):
            m.fts_or_query([])


if __name__ == "__main__":
    unittest.main()
