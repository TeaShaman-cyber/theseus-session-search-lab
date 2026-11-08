import pathlib
import subprocess
import sys
import tempfile
import unittest

from session_search.strict_json import StrictJsonError, loads_strict

ROOT = pathlib.Path(__file__).resolve().parents[1]


class StrictJsonTest(unittest.TestCase):
    def test_rejects_duplicate_object_keys(self):
        with self.assertRaisesRegex(StrictJsonError, "DUPLICATE_KEY:a"):
            loads_strict('{"a":1,"a":2}')

    def test_rejects_nonstandard_constants(self):
        for raw in ('{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}'):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(StrictJsonError, "NONSTANDARD_CONSTANT"):
                    loads_strict(raw)

    def test_rejects_trailing_json_content(self):
        with self.assertRaisesRegex(StrictJsonError, "JSON_INVALID"):
            loads_strict('{"a":1} {"b":2}')

    def test_accepts_normal_json(self):
        self.assertEqual(loads_strict('{"a":[1,true,null]}'), {"a": [1, True, None]})

    def run_gate(self, path: pathlib.Path):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "verify_strict_json.py"), str(path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

    def test_jsonl_gate_reports_duplicate_key_with_line_number(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "bad.jsonl"
            path.write_text('{"a":1}\n{"b":1,"b":2}\n', encoding="utf-8")
            proc = self.run_gate(path)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("bad.jsonl:2", proc.stderr)
        self.assertIn("DUPLICATE_KEY:b", proc.stderr)

    def test_jsonl_gate_rejects_blank_record(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "bad.jsonl"
            path.write_text('{"a":1}\n\n{"b":2}\n', encoding="utf-8")
            proc = self.run_gate(path)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("JSONL_EMPTY_LINE", proc.stderr)

    def test_json_gate_accepts_valid_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "ok.json"
            path.write_text('{"a":1}\n', encoding="utf-8")
            proc = self.run_gate(path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("STRICT_JSON_PASS", proc.stdout)


if __name__ == "__main__":
    unittest.main()
