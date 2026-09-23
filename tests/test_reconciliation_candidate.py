import importlib.util
import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "experiments" / "004-reconciliation-candidate" / "candidate.py"


def _load_candidate():
    spec = importlib.util.spec_from_file_location("reconciliation_candidate", CANDIDATE)
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate import failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReconciliationCandidateTest(unittest.TestCase):
    def test_candidate_improves_only_the_preregistered_gaps(self):
        candidate = _load_candidate()
        receipt = candidate.run_comparison()
        self.assertEqual(receipt["overall"], "PASS")
        cases = {case["id"]: case for case in receipt["cases"]}

        alias = cases["alias-canonicalization-gap"]
        self.assertEqual(alias["candidate"]["state"], "RECONCILED_CANDIDATE")
        self.assertEqual(alias["candidate"]["target_rank"], 1)
        self.assertEqual(alias["candidate"]["promotion_eligibility"], "CANDIDATE_ONLY")

        ambiguous = cases["unresolved-ambiguity"]
        self.assertEqual(ambiguous["candidate"]["state"], "AMBIGUOUS_CONCEPT")
        self.assertEqual(len(ambiguous["candidate"]["concept_matches"]), 2)
        self.assertEqual(ambiguous["candidate"]["promotion_eligibility"], "AMBIGUOUS")

        broad = cases["broad-context-candidate"]
        self.assertEqual(broad["candidate"]["state"], "RECALL_CANDIDATE")
        self.assertEqual(broad["candidate"]["promotion_eligibility"], "CANDIDATE_ONLY")

        exact = cases["exact-positive-control"]
        self.assertEqual(exact["candidate"]["state"], "DIRECT_WITH_RECALL_CANDIDATES")
        self.assertEqual(exact["candidate"]["target_rank"], 1)
        self.assertEqual(exact["candidate"]["promotion_eligibility"], "MIXED")
        self.assertEqual(
            exact["candidate"]["hits"][0]["promotion_eligibility"],
            "LEXICAL_EVIDENCE_ELIGIBLE",
        )
        self.assertTrue(
            all(
                hit["promotion_eligibility"] == "CANDIDATE_ONLY"
                for hit in exact["candidate"]["hits"]
                if hit["retrieval_basis"] == "RECALL_ONLY"
            )
        )

    def test_stale_registry_alias_does_not_fabricate_a_corpus_hit(self):
        candidate = _load_candidate()
        baseline = candidate._load_baseline_module()
        case = next(
            case for case in baseline._cases() if case["id"] == "alias-canonicalization-gap"
        )
        registry = candidate._load_registry()
        concept = next(
            item for item in registry["concepts"] if item["id"] == "retrieval-augmented-generation"
        )
        concept["aliases"] = ["RZXQ"]
        concept["forms"] = [concept["preferred_label"], "RZXQ"]
        concept["normalized_forms"] = [candidate._normalized(x) for x in concept["forms"]]

        with tempfile.TemporaryDirectory() as td:
            corpus = candidate._build_case_corpus(pathlib.Path(td), case, baseline)
            result = candidate.reconcile_search(corpus, case["query"], registry)

        self.assertEqual(result["state"], "UNKNOWN")
        self.assertEqual(result["promotion_eligibility"], "UNKNOWN")
        self.assertEqual(result["rows"], [])

    def test_committed_comparison_receipt_matches_current_candidate(self):
        candidate = _load_candidate()
        expected = json.loads(
            (CANDIDATE.parent / "comparison.public.json").read_text(encoding="utf-8")
        )
        self.assertEqual(candidate.run_comparison(), expected)


if __name__ == "__main__":
    unittest.main()
