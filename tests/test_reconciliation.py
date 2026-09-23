import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from session_search.corpus_store import ingest_artifact
from session_search.reconciliation import load_concept_registry, reconcile_search
from tests.test_corpus_store import _message, _write_capture

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _write_registry(path: pathlib.Path, concepts: list[dict]) -> pathlib.Path:
    path.write_text(
        json.dumps(
            {"schema": "theseus.session-search-controlled-concepts.v0", "concepts": concepts},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


class ReconciliationSearchTest(unittest.TestCase):
    def test_unique_alias_expansion_returns_candidate_only_hit(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            capture = _write_capture(
                root / "rag.zip",
                "rag-session",
                [_message("m1", "The RAG pipeline fetches evidence before composing an answer.", 1.0)],
                complete=True,
                title="RAG",
            )
            ingest_artifact(capture, corpus)
            registry = load_concept_registry(
                _write_registry(
                    root / "concepts.json",
                    [
                        {
                            "id": "retrieval-augmented-generation",
                            "preferred_label": "retrieval augmented generation",
                            "aliases": ["RAG"],
                        }
                    ],
                )
            )

            result = reconcile_search(
                corpus,
                "retrieval augmented generation",
                ["dialogue", "evidence"],
                8,
                registry,
            )

            self.assertEqual(result["state"], "RECONCILED_CANDIDATE")
            self.assertEqual(result["promotion_eligibility"], "CANDIDATE_ONLY")
            self.assertEqual(result["hits"][0]["session_id"], "rag-session")
            self.assertEqual(result["hits"][0]["retrieval_basis"], "CONTROLLED_ALIAS_EXPANSION")
            self.assertEqual(result["hits"][0]["promotion_eligibility"], "CANDIDATE_ONLY")

    def test_ambiguous_alias_does_not_choose_a_concept(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            for name, session, text in (
                ("planet", "mercury-planet", "Mercury is the innermost planet."),
                ("language", "mercury-language", "Mercury uses logic programming ideas."),
            ):
                ingest_artifact(
                    _write_capture(
                        root / f"{name}.zip",
                        session,
                        [_message(f"{name}-1", text, 1.0)],
                        complete=True,
                        title=name,
                    ),
                    corpus,
                )
            registry = load_concept_registry(
                _write_registry(
                    root / "concepts.json",
                    [
                        {"id": "mercury-planet", "preferred_label": "Mercury (planet)", "aliases": ["Mercury"]},
                        {"id": "mercury-language", "preferred_label": "Mercury (programming language)", "aliases": ["Mercury"]},
                    ],
                )
            )

            result = reconcile_search(corpus, "Mercury", ["dialogue", "evidence"], 8, registry)

            self.assertEqual(result["state"], "AMBIGUOUS_CONCEPT")
            self.assertEqual(result["promotion_eligibility"], "AMBIGUOUS")
            self.assertEqual(len(result["concept_matches"]), 2)
            self.assertEqual({hit["session_id"] for hit in result["hits"]}, {"mercury-planet", "mercury-language"})
            self.assertTrue(all(hit["promotion_eligibility"] == "AMBIGUOUS" for hit in result["hits"]))

    def test_mixed_strict_and_recall_hits_keep_per_hit_authority(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            ingest_artifact(
                _write_capture(
                    root / "target.zip",
                    "target",
                    [_message("t1", "Could we add a small IDE layer?", 1.0)],
                    complete=True,
                    title="target",
                ),
                corpus,
            )
            ingest_artifact(
                _write_capture(
                    root / "dump.zip",
                    "dump",
                    [_message("d1", "lightweight IDE reference output", 1.0, role="tool")],
                    complete=True,
                    title="dump",
                ),
                corpus,
            )
            registry = load_concept_registry(_write_registry(root / "concepts.json", []))

            result = reconcile_search(
                corpus,
                "lightweight IDE",
                ["dialogue", "evidence"],
                8,
                registry,
                recall=True,
            )

            self.assertEqual(result["state"], "DIRECT_WITH_RECALL_CANDIDATES")
            self.assertEqual(result["promotion_eligibility"], "MIXED")
            by_session = {hit["session_id"]: hit for hit in result["hits"]}
            self.assertEqual(by_session["dump"]["promotion_eligibility"], "LEXICAL_EVIDENCE_ELIGIBLE")
            self.assertEqual(by_session["target"]["promotion_eligibility"], "CANDIDATE_ONLY")

    def test_ambiguous_registry_without_corpus_hits_still_reports_ambiguity(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            ingest_artifact(
                _write_capture(
                    root / "other.zip",
                    "other",
                    [_message("o1", "Unrelated astronomy note.", 1.0)],
                    complete=True,
                    title="other",
                ),
                corpus,
            )
            registry = load_concept_registry(
                _write_registry(
                    root / "concepts.json",
                    [
                        {"id": "mercury-planet", "preferred_label": "Mercury (planet)", "aliases": ["Mercury"]},
                        {"id": "mercury-language", "preferred_label": "Mercury (programming language)", "aliases": ["Mercury"]},
                    ],
                )
            )

            result = reconcile_search(corpus, "Mercury", ["dialogue", "evidence"], 8, registry)

            self.assertEqual(result["state"], "AMBIGUOUS_CONCEPT")
            self.assertEqual(result["promotion_eligibility"], "AMBIGUOUS")
            self.assertEqual(result["hits"], [])


    def test_stale_alias_mapping_cannot_fabricate_hit(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            ingest_artifact(
                _write_capture(
                    root / "rag.zip",
                    "rag-session",
                    [_message("m1", "The RAG pipeline fetches evidence before composing an answer.", 1.0)],
                    complete=True,
                    title="RAG",
                ),
                corpus,
            )
            registry = load_concept_registry(
                _write_registry(
                    root / "concepts.json",
                    [
                        {
                            "id": "retrieval-augmented-generation",
                            "preferred_label": "retrieval augmented generation",
                            "aliases": ["RZXQ"],
                        }
                    ],
                )
            )

            result = reconcile_search(
                corpus,
                "retrieval augmented generation",
                ["dialogue", "evidence"],
                8,
                registry,
            )

            self.assertEqual(result["state"], "UNKNOWN")
            self.assertEqual(result["hits"], [])

    def test_cli_reconciliation_is_opt_in_and_returns_typed_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            ingest_artifact(
                _write_capture(
                    root / "rag.zip",
                    "rag-session",
                    [_message("m1", "The RAG pipeline fetches evidence before composing an answer.", 1.0)],
                    complete=True,
                    title="RAG",
                ),
                corpus,
            )
            registry = _write_registry(
                root / "concepts.json",
                [
                    {
                        "id": "retrieval-augmented-generation",
                        "preferred_label": "retrieval augmented generation",
                        "aliases": ["RAG"],
                    }
                ],
            )

            plain = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "session_search.search",
                    "retrieval augmented generation",
                    "--corpus",
                    str(corpus),
                    "--json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            reconciled = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "session_search.search",
                    "retrieval augmented generation",
                    "--corpus",
                    str(corpus),
                    "--reconcile-registry",
                    str(registry),
                    "--json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertEqual(json.loads(plain.stdout), [])
            self.assertEqual(reconciled.returncode, 0, reconciled.stderr)
            payload = json.loads(reconciled.stdout)
            self.assertEqual(payload["state"], "RECONCILED_CANDIDATE")
            self.assertEqual(payload["hits"][0]["session_id"], "rag-session")
            self.assertEqual(payload["hits"][0]["promotion_eligibility"], "CANDIDATE_ONLY")

    def test_legacy_db_rejects_reconciliation_registry(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            registry = _write_registry(root / "concepts.json", [])
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "session_search.search",
                    "test",
                    "--db",
                    str(root / "missing.sqlite3"),
                    "--reconcile-registry",
                    str(registry),
                    "--json",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("--reconcile-registry requires corpus search", proc.stderr)


if __name__ == "__main__":
    unittest.main()
