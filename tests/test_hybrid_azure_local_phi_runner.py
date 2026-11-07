import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT / "experiments" / "005-translation-provider-selection"
    / "hybrid_azure_local_phi_runner.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("issue178_hybrid", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HybridAzureLocalPhiTests(unittest.TestCase):
    def test_build_messages_contains_original_and_draft_but_no_oracle(self):
        mod = load_module()
        messages = mod.build_messages(
            query_en="Where is GitHub MCP optional?",
            azure_ru="Где GitHub MCP необязателен?",
        )
        text = "\n".join(m["content"] for m in messages)
        self.assertIn("Where is GitHub MCP optional?", text)
        self.assertIn("Где GitHub MCP необязателен?", text)
        self.assertIn("preserve", text.lower())
        self.assertNotIn("oracle", text.lower())
        self.assertNotIn("reference translation", text.lower())

    def test_runtime_metadata_is_explicit_not_hardcoded(self):
        mod = load_module()
        self.assertEqual(
            mod.runtime_metadata(
                model="microsoft/Phi-4-mini-instruct",
                quant="Q4_K_M",
                runtime="LM Studio native Windows",
            ),
            {
                "model": "microsoft/Phi-4-mini-instruct",
                "quant": "Q4_K_M",
                "runtime": "LM Studio native Windows",
            },
        )

    def test_parse_response_requires_one_stopped_nonempty_choice(self):
        mod = load_module()
        payload = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "Где GitHub MCP является необязательным?"},
            }],
            "usage": {"prompt_tokens": 50, "completion_tokens": 12},
        }
        parsed = mod.parse_phi_response(payload)
        self.assertEqual(
            parsed["repaired_ru"],
            "Где GitHub MCP является необязательным?",
        )
        self.assertEqual(parsed["prompt_tokens"], 50)
        self.assertEqual(parsed["completion_tokens"], 12)

        bad = {
            "choices": [{
                "finish_reason": "length",
                "message": {"content": ""},
            }]
        }
        with self.assertRaisesRegex(RuntimeError, "FINISH_REASON"):
            mod.parse_phi_response(bad)

    def test_validate_repaired_rows_preserves_case_and_target_mapping(self):
        mod = load_module()
        source = [
            {
                "case_id": "a",
                "query_text": "x",
                "azure_ru": "y",
                "target_content_fp": "fp1",
            },
            {
                "case_id": "b",
                "query_text": "u",
                "azure_ru": "v",
                "target_content_fp": "fp2",
            },
        ]
        repaired = [
            {
                "case_id": "a",
                "query_text": "x",
                "azure_ru": "y",
                "translated_ru": "z",
                "target_content_fp": "fp1",
                "latency_ms": 1.0,
            },
            {
                "case_id": "b",
                "query_text": "u",
                "azure_ru": "v",
                "translated_ru": "w",
                "target_content_fp": "fp2",
                "latency_ms": 2.0,
            },
        ]
        mod.validate_repaired_rows(source, repaired)

        drift = [dict(repaired[0], target_content_fp="wrong"), repaired[1]]
        with self.assertRaisesRegex(ValueError, "TARGET_MAPPING_DRIFT"):
            mod.validate_repaired_rows(source, drift)


if __name__ == "__main__":
    unittest.main()
