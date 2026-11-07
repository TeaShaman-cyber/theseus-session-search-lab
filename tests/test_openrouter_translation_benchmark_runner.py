import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "experiments"
    / "005-translation-provider-selection"
    / "openrouter_benchmark_runner.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "openrouter_translation_benchmark_runner", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class OpenRouterBenchmarkRunnerTests(unittest.TestCase):
    def test_candidate_arms_are_exact_and_do_not_use_router_alias(self):
        mod = load_module()

        self.assertEqual(
            [arm["name"] for arm in mod.CANDIDATE_ARMS],
            ["xing4", "gemma4", "nemotron3"],
        )
        for arm in mod.CANDIDATE_ARMS:
            self.assertNotEqual(arm["model"], "openrouter/free")
            self.assertTrue(arm["model"].endswith(":free"))
            self.assertTrue(arm["provider"])
            self.assertRegex(arm["schema"], r"^issue174_[a-z0-9_]+$")

    def test_request_pins_model_provider_and_translation_parameters(self):
        mod = load_module()
        arm = mod.CANDIDATE_ARMS[0]

        payload = mod.build_request_payload(
            arm=arm,
            query_text="Where is the session search configuration?",
        )

        self.assertEqual(payload["model"], arm["model"])
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["seed"], 127)
        self.assertEqual(payload["reasoning"], {"enabled": False})
        self.assertEqual(payload["provider"]["only"], [arm["provider"]])
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertNotIn("zdr", payload["provider"])
        self.assertNotIn("data_collection", payload["provider"])
        self.assertIn("Output only", payload["messages"][0]["content"])
        self.assertEqual(
            payload["messages"][1]["content"],
            "Where is the session search configuration?",
        )

    def test_parse_response_rejects_model_or_provider_drift(self):
        mod = load_module()
        arm = mod.CANDIDATE_ARMS[1]
        good = {
            "model": arm["model"],
            "provider": "Google AI Studio",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "Где конфигурация поиска по сессиям?"},
                }
            ],
            "usage": {"cost": 0, "completion_tokens_details": {"reasoning_tokens": 0}},
        }

        parsed = mod.parse_response(arm=arm, payload=good)
        self.assertEqual(parsed["translated_ru"], "Где конфигурация поиска по сессиям?")
        self.assertEqual(parsed["cost"], 0)

        wrong_model = dict(good, model="openrouter/free")
        with self.assertRaisesRegex(RuntimeError, "MODEL_DRIFT"):
            mod.parse_response(arm=arm, payload=wrong_model)

        wrong_provider = dict(good, provider="Other Provider")
        with self.assertRaisesRegex(RuntimeError, "PROVIDER_DRIFT"):
            mod.parse_response(arm=arm, payload=wrong_provider)

    def test_translation_attempts_continue_after_one_provider_failure(self):
        mod = load_module()
        calls = []

        def fake_translate(*, arm, en_rows, endpoint):
            calls.append(arm["name"])
            if arm["name"] == "xing4":
                raise mod.ProviderCallError("OPENROUTER_HTTP_429_CODE_429")
            return [
                {
                    "case_id": "case-a",
                    "query_text": "q",
                    "translated_ru": "п",
                    "target_content_fp": "f" * 32,
                    "latency_ms": 1.0,
                    "cost": 0,
                    "response_model": arm["model"],
                    "response_provider": arm["response_provider"],
                }
            ]

        successes, failures = mod.run_translation_attempts(
            en_rows=[{"case_id": "case-a"}],
            endpoint="https://example.test",
            translator=fake_translate,
        )

        self.assertEqual(calls, ["xing4", "gemma4", "nemotron3"])
        self.assertEqual(set(successes), {"gemma4", "nemotron3"})
        self.assertEqual(
            failures,
            {"xing4": "OPENROUTER_HTTP_429_CODE_429"},
        )

    def test_free_model_start_interval_is_explicit(self):
        mod = load_module()
        self.assertEqual(
            mod.translate_rows.__kwdefaults__["min_start_interval_seconds"],
            3.2,
        )

    def test_parse_response_rejects_reasoning_only_or_truncated_output(self):
        mod = load_module()
        arm = mod.CANDIDATE_ARMS[2]

        with self.assertRaisesRegex(RuntimeError, "FINISH_REASON"):
            mod.parse_response(
                arm=arm,
                payload={
                    "model": arm["model"],
                    "provider": "Nvidia",
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": ""},
                        }
                    ],
                    "usage": {},
                },
            )


if __name__ == "__main__":
    unittest.main()
