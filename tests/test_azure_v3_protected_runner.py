import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "005-translation-provider-selection"
    / "azure_v3_protected_runner.py"
)


def load():
    spec = importlib.util.spec_from_file_location("azure_v3_protected_runner", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ProtectedAzureV3Tests(unittest.TestCase):
    def test_protects_only_known_entities_and_escapes_html(self):
        m = load()
        markup, protected = m.markup_query('Compare Basic Memory and GitHub vs <untrusted> A&B.')
        self.assertIn('<span class="notranslate">Basic Memory</span>', markup)
        self.assertIn('<span class="notranslate">GitHub</span>', markup)
        self.assertIn('&lt;untrusted&gt;', markup)
        self.assertIn('A&amp;B', markup)
        self.assertEqual(protected, ('Basic Memory', 'GitHub'))
        self.assertNotIn('notranslate">Compare', markup)

    def test_prevents_substring_matches_and_can_preserve_acronyms(self):
        m = load()
        markup, terms = m.markup_query('GitHubber OAuth2 MCP and GitHub')
        self.assertEqual(terms, ('MCP', 'GitHub'))
        self.assertNotIn('notranslate">GitHubber', markup)

    def test_strips_valid_markup_and_verifies_unchanged_protected_tokens(self):
        m = load()
        txt = '<div>Проверить <span class="notranslate">GitHub</span> &amp; перевод.</div>'
        self.assertEqual(m.clean_translation(txt, ('GitHub',)), 'Проверить GitHub & перевод.')
        with self.assertRaisesRegex(ValueError, 'PROTECTED_TERM_MISSING'):
            m.clean_translation('<div>Проверить Гитхаб</div>', ('GitHub',))

    def test_rejects_unexpected_markup_and_empty_output(self):
        m = load()
        with self.assertRaisesRegex(ValueError, 'UNEXPECTED_HTML_TAG'):
            m.clean_translation('<div>Текст<script>bad</script></div>', ())
        with self.assertRaisesRegex(ValueError, 'EMPTY_TRANSLATION'):
            m.clean_translation('<div> </div>', ())

    def test_protected_request_is_html_mode_and_output_is_clean(self):
        import json
        from unittest.mock import patch
        m = load()
        calls = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps([{
                    "translations": [{"text": '<div>Проверка <span class="notranslate">GitHub</span>.</div>'}]
                }]).encode("utf-8")

        def fake_opener(request, timeout):
            calls.append(request)
            return FakeResponse()

        rows = [{"case_id": "a", "query_text": "Check GitHub.", "target_content_fp": "a" * 32}]
        with patch.object(m.urllib.request, "urlopen", side_effect=fake_opener):
            translated = m._protected_translate(
                rows, token="dummy", endpoint="https://example.test/translate?api-version=3.0&from=en&to=ru"
            )
        self.assertIn("&textType=html", calls[0].full_url)
        body = json.loads(calls[0].data)
        self.assertIn('class=\"notranslate\"', body[0]["Text"])
        self.assertEqual(translated[0]["translated_ru"], "Проверка GitHub.")
        self.assertEqual(translated[0]["protected_terms"], 1)

    def test_private_bundle_is_mode_0600_and_write_once(self):
        import os
        import tempfile
        m = load()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private.json"
            m._save_private(path, {"gold": "secret"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                m._save_private(path, {"gold": "overwrite"})
            self.assertIn("secret", path.read_text())

    def test_schema_is_validated(self):
        m = load()
        self.assertEqual(m.eval_schema('plain'), 'issue182_v3_plain')
        with self.assertRaisesRegex(ValueError, 'UNKNOWN_ARM'):
            m.eval_schema('issue127_eval')


if __name__ == '__main__':
    unittest.main()
