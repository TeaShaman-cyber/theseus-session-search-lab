import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "experiments"
    / "005-translation-provider-selection"
    / "azure_benchmark_runner.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "azure_translation_benchmark_runner", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AzureTranslationBenchmarkRunnerTests(unittest.TestCase):
    def test_postgres_runtime_keeps_password_out_of_process_arguments(self):
        mod = load_module()

        runtime = mod.postgres_runtime(
            "postgresql://bench_user:s3cr%40t@db.example.test:5433/neondb?sslmode=require"
        )

        self.assertEqual(
            runtime.argv_prefix,
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-At"],
        )
        self.assertEqual(runtime.env["PGHOST"], "db.example.test")
        self.assertEqual(runtime.env["PGPORT"], "5433")
        self.assertEqual(runtime.env["PGDATABASE"], "neondb")
        self.assertEqual(runtime.env["PGUSER"], "bench_user")
        self.assertEqual(runtime.env["PGPASSWORD"], "s3cr@t")
        self.assertEqual(runtime.env["PGSSLMODE"], "require")
        self.assertNotIn("s3cr", " ".join(runtime.argv_prefix))

    def test_neon_api_runtime_resolves_endpoint_and_secret_only_in_env(self):
        mod = load_module()
        calls = []

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                import json

                return json.dumps(self.payload).encode("utf-8")

        def opener(request, timeout):
            url = request.full_url
            calls.append(url)
            if url.endswith("/endpoints"):
                return FakeResponse(
                    {
                        "endpoints": [
                            {
                                "branch_id": "br-target",
                                "type": "read_write",
                                "host": "ep-target.example.neon.tech",
                            }
                        ]
                    }
                )
            if url.endswith(
                "/branches/br-target/roles/session_search_owner/reveal_password"
            ):
                return FakeResponse({"password": "runtime-secret"})
            raise AssertionError(url)

        runtime = mod.postgres_runtime_from_neon_api(
            api_base="https://neon-proxy.example/api/v2",
            project_id="project-a",
            branch_id="br-target",
            role_name="session_search_owner",
            opener=opener,
        )

        self.assertEqual(runtime.env["PGHOST"], "ep-target.example.neon.tech")
        self.assertEqual(runtime.env["PGDATABASE"], "neondb")
        self.assertEqual(runtime.env["PGUSER"], "session_search_owner")
        self.assertEqual(runtime.env["PGPASSWORD"], "runtime-secret")
        self.assertNotIn("runtime-secret", " ".join(runtime.argv_prefix))
        self.assertEqual(len(calls), 2)

    def test_validate_frozen_gold_requires_expected_shape_and_digest(self):
        mod = load_module()
        rows = []
        for idx in range(10):
            case_id = f"case-{idx}"
            rows.append(
                {
                    "case_id": case_id,
                    "query_lang": "en",
                    "query_text": f"en-{idx}",
                    "target_content_fp": f"{idx:032x}",
                }
            )
            rows.append(
                {
                    "case_id": case_id,
                    "query_lang": "ru",
                    "query_text": f"ru-{idx}",
                    "target_content_fp": f"{idx:032x}",
                }
            )

        digest = mod.gold_digest(rows)
        summary = mod.validate_frozen_gold(rows, expected_digest=digest)

        self.assertEqual(summary["rows"], 20)
        self.assertEqual(summary["cases"], 10)
        self.assertEqual(summary["en"], 10)
        self.assertEqual(summary["ru"], 10)
        self.assertEqual(summary["gold_digest_v2"], digest)

        with self.assertRaisesRegex(ValueError, "GOLD_DIGEST_MISMATCH"):
            mod.validate_frozen_gold(rows, expected_digest="0" * 64)

    def test_translation_freeze_copy_meta_command_is_single_line(self):
        mod = load_module()

        script = mod.translation_freeze_script(Path("/tmp/issue127.csv"))
        copy_lines = [
            line for line in script.splitlines() if line.startswith("\\copy ")
        ]

        self.assertEqual(len(copy_lines), 1)
        self.assertIn(
            "\\copy issue127_eval.translations "
            "(case_id,translated_ru,target_content_fp,latency_ms,source_chars) "
            "from '/tmp/issue127.csv' with (format csv, header true);",
            copy_lines[0],
        )

    def test_public_receipt_excludes_private_translation_fields(self):
        mod = load_module()
        provider_rows = [
            {
                "case_id": "case-a",
                "query_text": "private english query",
                "translated_ru": "приватный перевод",
                "target_content_fp": "f" * 32,
                "latency_ms": 100.0,
            },
            {
                "case_id": "case-b",
                "query_text": "another private query",
                "translated_ru": "другой приватный перевод",
                "target_content_fp": "e" * 32,
                "latency_ms": 200.0,
            },
        ]
        ranks = {"case-a": 1, "case-b": 4}

        receipt = mod.public_receipt(
            provider_rows=provider_rows,
            ranks=ranks,
            gold_digest_v2="a" * 64,
            translation_digest_v2="b" * 64,
            historical_translation_digest="c" * 64,
        )

        serialized = repr(receipt)
        self.assertNotIn("query_text", serialized)
        self.assertNotIn("translated_ru", serialized)
        self.assertNotIn("target_content_fp", serialized)
        self.assertNotIn("private english query", serialized)
        self.assertNotIn("приватный перевод", serialized)
        self.assertEqual(receipt["provider_calls"], 2)
        self.assertEqual(receipt["provider_success"], 2)
        self.assertEqual(receipt["source_chars"], 42)
        self.assertEqual(receipt["r_at_1"], 0.5)
        self.assertEqual(receipt["r_at_3"], 0.5)
        self.assertEqual(receipt["r_at_5"], 1.0)
        self.assertEqual(receipt["r_at_10"], 1.0)
        self.assertEqual(receipt["mrr"], 0.625)


if __name__ == "__main__":
    unittest.main()
