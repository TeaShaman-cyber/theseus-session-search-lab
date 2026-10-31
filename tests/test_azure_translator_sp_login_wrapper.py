import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "azure_translator_sp_login.sh"


class AzureTranslatorServicePrincipalWrapperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.creds = self.base / "creds"
        self.creds.mkdir()
        self.key = self.creds / "sp-auth-key.pem"
        self.cert = self.creds / "sp-auth-cert.pem"
        self.key.write_text("FAKE_PRIVATE_KEY_DO_NOT_PRINT\n", encoding="utf-8")
        self.cert.write_text("FAKE_PUBLIC_CERT_DO_NOT_PRINT\n", encoding="utf-8")
        self.key.chmod(0o600)
        self.cert.chmod(0o600)

        self.az = self.base / "fake-az"
        self.az.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                set -eu
                cmd="$1"
                shift
                case "$cmd" in
                  login)
                    printf '%s\n' "$@" > "$FAKE_AZ_LOGIN_ARGS"
                    exit 0
                    ;;
                  resource)
                    test "$1" = show
                    printf '%s\n' 'theseus-translator'
                    exit 0
                    ;;
                  *)
                    echo "unexpected fake az command: $cmd" >&2
                    exit 9
                    ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        self.az.chmod(self.az.stat().st_mode | stat.S_IXUSR)
        self.login_args = self.base / "login-args.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        env = os.environ.copy()
        env.update(
            {
                "AZURE_TRANSLATOR_CREDENTIAL_DIR": str(self.creds),
                "AZURE_TRANSLATOR_AZ": str(self.az),
                "AZURE_CONFIG_DIR": str(self.base / "az-config"),
                "FAKE_AZ_LOGIN_ARGS": str(self.login_args),
            }
        )
        return subprocess.run(
            [str(WRAPPER)], text=True, capture_output=True, env=env
        )

    def test_login_uses_certificate_and_verifies_expected_resource_without_secret_output(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("AZURE_TRANSLATOR_SP_LOGIN_VERIFIED", proc.stdout)
        self.assertNotIn("FAKE_PRIVATE_KEY_DO_NOT_PRINT", proc.stdout + proc.stderr)
        self.assertNotIn("FAKE_PUBLIC_CERT_DO_NOT_PRINT", proc.stdout + proc.stderr)

        args = self.login_args.read_text(encoding="utf-8")
        self.assertIn("--service-principal", args)
        self.assertIn("--username", args)
        self.assertIn("8a70c774-a3cb-4bdf-8a72-d3fb63611a3f", args)
        self.assertIn("--tenant", args)
        self.assertIn("673ee0ec-b9c2-48e3-86c9-0737bd2e99a9", args)
        self.assertIn("--certificate", args)
        self.assertIn("sp-auth-login.pem", args)

    def test_missing_private_key_fails_closed(self):
        self.key.unlink()
        proc = self._run()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("private key missing", proc.stderr.lower())
        self.assertFalse(self.login_args.exists())


if __name__ == "__main__":
    unittest.main()
