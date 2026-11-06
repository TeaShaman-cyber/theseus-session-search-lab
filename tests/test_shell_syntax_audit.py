import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AUDIT = Path(__file__).resolve().parents[1] / "scripts" / "verify_shell_syntax.py"


class ShellSyntaxAuditTest(unittest.TestCase):
    def audit_repo(self, test_files):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            for rel, content in test_files.items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            return subprocess.run([sys.executable, str(AUDIT)], cwd=root, capture_output=True, text=True)

    def test_extensionless_shell_file_syntax_is_checked(self):
        valid = self.audit_repo({"tools/dev/check": "#!/bin/sh\nset -eu\necho ok\n"})
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn("SHELL_SYNTAX_CHECKED=1", valid.stdout)

        invalid = self.audit_repo({"tools/dev/check": "#!/bin/sh\nif true; then\n  echo broken\n"})
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("SHELL_SYNTAX_FAILED: tools/dev/check", invalid.stderr)

    def test_extensionless_test_file_is_not_silently_skipped(self):
        result = self.audit_repo({
            "tools/dev/check": "#!/bin/sh\n: \n",
            "tests/test_without_suffix": "def broken(\n",
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UNCLASSIFIED_EXTENSIONLESS_TEST: tests/test_without_suffix", result.stderr)


if __name__ == "__main__":
    unittest.main()
