import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parents[1] / "experiments" / "005-translation-provider-selection"


def load():
    p = HERE / "azure_v3_dynamic_dictionary_runner.py"
    spec = importlib.util.spec_from_file_location("issue184_dynamic", p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DynamicDictionaryTests(unittest.TestCase):
    def test_markup_preserves_original_plain_text_outside_terms(self):
        m = load()
        src = "Compare Basic Memory with GitHub MCP & local <flag>."
        marked, terms = m.dictionary_markup(src)
        self.assertEqual(terms, ("Basic Memory", "GitHub", "MCP"))
        self.assertIn('<mstrans:dictionary translation="Basic Memory">Basic Memory</mstrans:dictionary>', marked)
        self.assertIn('<mstrans:dictionary translation="GitHub">GitHub</mstrans:dictionary>', marked)
        self.assertIn(' & local <flag>.', marked)

    def test_no_substrings_or_freeform_glossary(self):
        m = load()
        marked, terms = m.dictionary_markup("GitHubber OAuth2 MCP and GitHub")
        self.assertEqual(terms, ("MCP", "GitHub"))
        self.assertIn("GitHubber OAuth2", marked)

    def test_mock_http_uses_existing_v3_plain_url_and_validates(self):
        m = load()
        calls = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps([{"translations": [{"text": "Как работает Basic Memory?"}]}]).encode()

        def fake_open(req, timeout):
            calls.append(req)
            return FakeResponse()

        rows = [{"case_id": "a", "query_text": "How does Basic Memory work?", "target_content_fp": "a"*32}]
        with patch.object(m.urllib.request, "urlopen", side_effect=fake_open):
            res = m.translate_rows(rows, token="redacted", endpoint=m._AZURE.DEFAULT_TRANSLATOR_ENDPOINT)
        self.assertNotIn("textType=html", calls[0].full_url)
        self.assertIn("from=en&to=ru", calls[0].full_url)
        self.assertIn("<mstrans:dictionary", json.loads(calls[0].data)[0]["Text"])
        self.assertEqual(res[0]["translated_ru"], "Как работает Basic Memory?")

    def test_fails_if_dictionary_ignored_or_leaked(self):
        m = load()
        with self.assertRaisesRegex(ValueError, "DICT_TERM_MISSING"):
            m.validate_output("Как работает базовая память?", ("Basic Memory",))
        with self.assertRaisesRegex(ValueError, "DICT_TAG_LEAK"):
            m.validate_output("<mstrans:dictionary>Basic Memory</mstrans:dictionary>", ("Basic Memory",))

    def test_rejects_escaped_dictionary_tag_leak(self):
        m = load()
        with self.assertRaisesRegex(ValueError, "DICT_TAG_LEAK"):
            m.validate_output("&lt;mstrans:dictionary&gt;GitHub&lt;/mstrans:dictionary&gt;", ("GitHub",))

    def test_reject_schema_drift_and_safe_private_write(self):
        m = load()
        self.assertEqual(m.EVAL_SCHEMA, "issue184_v3_dynamic")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "result.json"
            m.save_private(path, {"rows": [1]})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                m.save_private(path, {"rows": [2]})


if __name__ == "__main__":
    unittest.main()
