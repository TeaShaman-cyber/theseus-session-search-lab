import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "experiments"
    / "005-translation-provider-selection"
    / "bothub_benchmark_runner.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "bothub_translation_benchmark_runner", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BotHubBenchmarkRunnerTests(unittest.TestCase):
    def test_candidate_arms_pin_exact_paid_models_and_price_snapshot(self):
        mod = load_module()
        self.assertEqual(
            [arm["model"] for arm in mod.CANDIDATE_ARMS],
            [
                "gemini-2.5-flash-lite",
                "gpt-4.1-nano",
                "deepseek-v4.1-flash",
            ],
        )
        expected = {
            "gemini-2.5-flash-lite": (4.38, 17.53),
            "gpt-4.1-nano": (4.38, 17.53),
            "deepseek-v4.1-flash": (4.25, 47.21),
        }
        for arm in mod.CANDIDATE_ARMS:
            self.assertEqual(
                (arm["input_rub_per_m"], arm["output_rub_per_m"]),
                expected[arm["model"]],
            )
            self.assertRegex(arm["schema"], r"^issue176_[a-z0-9_]+$")

    def test_request_is_translation_only_and_exact_model(self):
        mod = load_module()
        arm = mod.CANDIDATE_ARMS[0]
        payload = mod.build_request_payload(
            arm=arm,
            query_text="Where is the provider configuration?",
        )
        self.assertEqual(payload["model"], arm["model"])
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["max_tokens"], 512)
        self.assertFalse(payload["stream"])
        self.assertIn("Output only", payload["messages"][0]["content"])
        self.assertEqual(
            payload["messages"][1]["content"],
            "Where is the provider configuration?",
        )

    def test_parse_response_captures_usage_and_rejects_drift(self):
        mod = load_module()
        arm = mod.CANDIDATE_ARMS[0]
        payload = {
            "model": arm["model"],
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "Где находится конфигурация провайдера?"},
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cost": None,
                "completion_tokens_details": {"reasoning_tokens": 7},
            },
        }
        parsed = mod.parse_response(arm=arm, payload=payload)
        self.assertEqual(parsed["prompt_tokens"], 100)
        self.assertEqual(parsed["completion_tokens"], 20)
        self.assertEqual(parsed["reasoning_tokens"], 7)
        self.assertEqual(parsed["total_tokens"], 120)
        self.assertIsNone(parsed["provider_cost"])

        with self.assertRaisesRegex(RuntimeError, "MODEL_DRIFT"):
            mod.parse_response(arm=arm, payload=dict(payload, model="other"))

        bad = dict(payload)
        bad["choices"] = [
            {"finish_reason": "length", "message": {"content": ""}}
        ]
        with self.assertRaisesRegex(RuntimeError, "FINISH_REASON"):
            mod.parse_response(arm=arm, payload=bad)

    def test_estimated_rub_uses_reported_prompt_and_completion_tokens(self):
        mod = load_module()
        arm = {
            "input_rub_per_m": 4.0,
            "output_rub_per_m": 20.0,
        }
        rows = [
            {"prompt_tokens": 1000, "completion_tokens": 200},
            {"prompt_tokens": 500, "completion_tokens": 100},
        ]
        self.assertAlmostEqual(
            mod.estimated_rub(arm=arm, provider_rows=rows),
            0.012,
        )


if __name__ == "__main__":
    unittest.main()
