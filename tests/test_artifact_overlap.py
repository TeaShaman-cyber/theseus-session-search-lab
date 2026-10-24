import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from session_search.artifact_overlap import (
    audit_provider_sets,
    classify_overlap,
    load_accepted_ledger,
    load_frozen_provider_manifest,
    set_digest,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


class OverlapClassificationTest(unittest.TestCase):
    def test_disjoint(self):
        self.assertEqual(classify_overlap({SHA_A}, {SHA_B})["status"], "DISJOINT")

    def test_full_overlap(self):
        result = classify_overlap({SHA_A, SHA_B}, {SHA_B, SHA_A})
        self.assertEqual(result["status"], "FULL_OVERLAP")
        self.assertEqual(result["overlap_count"], 2)
        self.assertEqual(result["novel_count"], 0)

    def test_partial_overlap(self):
        result = classify_overlap({SHA_A}, {SHA_A, SHA_B})
        self.assertEqual(result["status"], "PARTIAL_OVERLAP")
        self.assertEqual(result["overlap_count"], 1)
        self.assertEqual(result["novel_count"], 1)

    def test_empty_candidate(self):
        self.assertEqual(classify_overlap({SHA_A}, set())["status"], "EMPTY")

    def test_digest_is_order_independent(self):
        self.assertEqual(set_digest({SHA_A, SHA_B}), set_digest({SHA_B, SHA_A}))

    def test_public_result_contains_only_set_digests_not_individual_hashes(self):
        result = classify_overlap({SHA_A}, {SHA_A, SHA_B})
        payload = json.dumps(result, sort_keys=True)
        self.assertNotIn(SHA_A, payload)
        self.assertNotIn(SHA_B, payload)
        self.assertIn("candidate_set_sha256", result)


class EvidenceReadersTest(unittest.TestCase):
    def test_load_accepted_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            accepted = root / "ledger" / "accepted"
            accepted.mkdir(parents=True)
            (accepted / f"{SHA_A}.json").write_text(json.dumps({"artifact_sha256": SHA_A}))
            (accepted / f"{SHA_B}.json").write_text(json.dumps({"artifact_sha256": SHA_B}))
            self.assertEqual(load_accepted_ledger(root), {SHA_A, SHA_B})

    def test_ledger_filename_and_payload_must_match(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            accepted = root / "ledger" / "accepted"
            accepted.mkdir(parents=True)
            (accepted / f"{SHA_A}.json").write_text(json.dumps({"artifact_sha256": SHA_B}))
            with self.assertRaisesRegex(ValueError, "LEDGER_IDENTITY_MISMATCH"):
                load_accepted_ledger(root)

    def test_frozen_manifest_provider_sets(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            path.write_text(json.dumps({
                "providers": {
                    "chatgpt": {"artifacts": [{"sha256": SHA_A}, {"sha256": SHA_B}]},
                    "xai": {"artifacts": [{"sha256": SHA_C}]},
                }
            }))
            self.assertEqual(load_frozen_provider_manifest(path), {
                "chatgpt": {SHA_A, SHA_B},
                "xai": {SHA_C},
            })

    def test_invalid_sha_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            path.write_text(json.dumps({"providers": {"xai": {"artifacts": [{"sha256": "bad"}]}}}))
            with self.assertRaisesRegex(ValueError, "INVALID_ARTIFACT_SHA256"):
                load_frozen_provider_manifest(path)


    def test_same_identity_across_providers_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            path.write_text(json.dumps({
                "providers": {
                    "chatgpt": {"artifacts": [{"sha256": SHA_A}]},
                    "xai": {"artifacts": [{"sha256": SHA_A}]},
                }
            }))
            with self.assertRaisesRegex(ValueError, "ARTIFACT_PROVIDER_CONFLICT"):
                load_frozen_provider_manifest(path)

    def test_duplicate_identity_in_provider_manifest_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            path.write_text(json.dumps({
                "providers": {"xai": {"artifacts": [{"sha256": SHA_A}, {"sha256": SHA_A}]}}
            }))
            with self.assertRaisesRegex(ValueError, "DUPLICATE_ARTIFACT_IDENTITY"):
                load_frozen_provider_manifest(path)


class ProviderAuditTest(unittest.TestCase):
    def test_provider_audit_is_sorted_and_aggregate_only(self):
        result = audit_provider_sets(
            {SHA_A, SHA_B},
            {"xai": {SHA_C}, "deepseek": {SHA_B}},
        )
        self.assertEqual([x["provider"] for x in result["providers"]], ["deepseek", "xai"])
        self.assertEqual(result["providers"][0]["status"], "FULL_OVERLAP")
        self.assertEqual(result["providers"][1]["status"], "DISJOINT")
        self.assertEqual(result["authority_count"], 2)
        self.assertNotIn(SHA_A, json.dumps(result))


class OverlapCliTest(unittest.TestCase):
    @staticmethod
    def clean_env():
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        return env

    def test_help_works_without_pythonpath(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "scripts/audit_corpus_overlap.py", "--help"],
            cwd=root,
            env=self.clean_env(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Audit immutable accepted-artifact overlap", result.stdout)

    def test_synthetic_cli_run_is_public_safe(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = td / "corpus"
            accepted = corpus / "ledger" / "accepted"
            accepted.mkdir(parents=True)
            (accepted / f"{SHA_A}.json").write_text(
                json.dumps({"artifact_sha256": SHA_A})
            )
            manifest = td / "manifest.json"
            manifest.write_text(json.dumps({
                "providers": {
                    "chatgpt": {"artifacts": [{"sha256": SHA_A}]},
                    "xai": {"artifacts": [{"sha256": SHA_B}]},
                }
            }))
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/audit_corpus_overlap.py",
                    "--authority-corpus",
                    str(corpus),
                    "--candidate-manifest",
                    str(manifest),
                ],
                cwd=root,
                env=self.clean_env(),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(
                [(item["provider"], item["status"]) for item in payload["providers"]],
                [("chatgpt", "FULL_OVERLAP"), ("xai", "DISJOINT")],
            )
            self.assertNotIn(SHA_A, result.stdout)
            self.assertNotIn(SHA_B, result.stdout)

    def test_partial_overlap_cli_exits_nonzero_after_printing_evidence(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            corpus = td / "corpus"
            accepted = corpus / "ledger" / "accepted"
            accepted.mkdir(parents=True)
            (accepted / f"{SHA_A}.json").write_text(json.dumps({"artifact_sha256": SHA_A}))
            manifest = td / "manifest.json"
            manifest.write_text(json.dumps({
                "providers": {
                    "xai": {"artifacts": [{"sha256": SHA_A}, {"sha256": SHA_B}]}
                }
            }))
            result = subprocess.run(
                [sys.executable, "scripts/audit_corpus_overlap.py",
                 "--authority-corpus", str(corpus),
                 "--candidate-manifest", str(manifest)],
                cwd=root, env=self.clean_env(), capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["providers"][0]["status"], "PARTIAL_OVERLAP")


if __name__ == "__main__":
    unittest.main()
