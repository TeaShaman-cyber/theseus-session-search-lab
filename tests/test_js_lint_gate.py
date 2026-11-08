import pathlib
import tempfile
import unittest

from scripts.verify_js_lint import lint_paths


class JsLintGateTest(unittest.TestCase):
    def test_valid_module_passes(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "valid.mjs"
            path.write_text("export const answer = 42;\n", encoding="utf-8")
            result = lint_paths([path])
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_valid_json_passes(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "valid.json"
            path.write_text('{"a":1,"b":[true,null]}\n', encoding="utf-8")
            result = lint_paths([path])
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_json_key_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "duplicate.json"
            path.write_text('{"a":1,"a":2}\n', encoding="utf-8")
            result = lint_paths([path])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("noDuplicateObjectKeys", result.stderr)

    def test_nonstandard_json_constant_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "nan.json"
            path.write_text('{"a":NaN}\n', encoding="utf-8")
            result = lint_paths([path])
            self.assertNotEqual(result.returncode, 0)

    def test_undeclared_identifier_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "undeclared.mjs"
            path.write_text(
                "export function value(){ return missingName; }\n",
                encoding="utf-8",
            )
            result = lint_paths([path])
            self.assertNotEqual(result.returncode, 0)

    def test_syntax_error_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "broken.mjs"
            path.write_text("export const answer = ;\n", encoding="utf-8")
            result = lint_paths([path])
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
