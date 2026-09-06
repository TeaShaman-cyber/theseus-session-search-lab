import json
import os
import pathlib
import subprocess
import tempfile
import unittest

from session_search.corpus_store import ingest_artifact
from session_search.importer import import_export
from session_search.search import search, search_corpus
from tests.test_bootstrap_contract import synthetic_export
from tests.test_corpus_store import _message, _write_capture

ROOT = pathlib.Path(__file__).resolve().parents[1]


def build_two_session_corpus(root: pathlib.Path, phrase: str) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    corpus = root / "corpus"
    a = _write_capture(
        root / "a.zip",
        "session-a",
        [_message("a1", f"{phrase} from alpha", 1.0, role="user")],
        complete=True,
        title="Alpha",
    )
    b = _write_capture(
        root / "b.zip",
        "session-b",
        [_message("b1", f"{phrase} from beta", 2.0, role="assistant")],
        complete=False,
        title="Beta",
    )
    ingest_artifact(a, corpus)
    ingest_artifact(b, corpus)
    return corpus


class CorpusSearchTest(unittest.TestCase):
    def test_global_search_returns_hits_from_multiple_sessions_with_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = build_two_session_corpus(pathlib.Path(td), "copper compass")
            rows = search_corpus(corpus, "copper compass", ["dialogue", "evidence"], 10)
            self.assertEqual({row["session_id"] for row in rows}, {"session-a", "session-b"})
            self.assertEqual({row["session_title"] for row in rows}, {"Alpha", "Beta"})
            self.assertEqual(
                {row["session_coverage"] for row in rows},
                {"COMPLETE_EXPOSED_CONVERSATION", "PARTIAL_SESSION_SLICE"},
            )

    def test_session_filter_limits_hits_to_one_session(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = build_two_session_corpus(pathlib.Path(td), "copper compass")
            rows = search_corpus(
                corpus,
                "copper compass",
                ["dialogue", "evidence"],
                10,
                session_id="session-b",
            )
            self.assertTrue(rows)
            self.assertEqual({row["session_id"] for row in rows}, {"session-b"})

    def test_recall_mode_expands_candidates_without_changing_strict_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            target = _write_capture(
                root / "target.zip",
                "session-target",
                [
                    _message("t1", "Could we add a small IDE layer?", 1.0, role="user"),
                    _message("t2", "The dev kit uses ruff shellcheck and shfmt.", 2.0, role="assistant"),
                ],
                complete=True,
                title="Dev Kit",
            )
            distractor = _write_capture(
                root / "distractor.zip",
                "session-distractor",
                [_message("d1", "lightweight IDE reference output", 1.0, role="tool")],
                complete=True,
                title="Tool dump",
            )
            ingest_artifact(target, corpus)
            ingest_artifact(distractor, corpus)

            strict = search_corpus(corpus, "lightweight IDE", ["dialogue", "evidence"], 8)
            recalled = search_corpus(
                corpus, "lightweight IDE", ["dialogue", "evidence"], 8, recall=True
            )

            self.assertEqual([row["session_id"] for row in strict], ["session-distractor"])
            self.assertIn("session-target", {row["session_id"] for row in recalled})

    def test_recall_ranks_dialogue_session_ahead_of_single_evidence_dump(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            target = _write_capture(
                root / "target.zip",
                "session-target",
                [
                    _message("t1", "lightweight IDE layer for development", 1.0, role="user"),
                    _message("t2", "IDE tooling with ruff", 2.0, role="assistant"),
                ],
                complete=True,
                title="Dev Kit",
            )
            dump = _write_capture(
                root / "dump.zip",
                "session-dump",
                [_message("d1", ("lightweight IDE " * 200).strip(), 1.0, role="tool")],
                complete=True,
                title="Large evidence dump",
            )
            ingest_artifact(target, corpus)
            ingest_artifact(dump, corpus)

            rows = search_corpus(
                corpus, "lightweight IDE", ["dialogue", "evidence"], 8, recall=True
            )

            self.assertTrue(rows)
            self.assertEqual(rows[0]["session_id"], "session-target")
            self.assertEqual(rows[0]["search_class"], "dialogue")

    def test_recall_output_keeps_session_diversity_when_one_session_has_many_hits(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            dominant = _write_capture(
                root / "dominant.zip",
                "session-dominant",
                [
                    _message(f"d{i}", f"lightweight IDE reference {i}", float(i), role="assistant")
                    for i in range(1, 31)
                ],
                complete=True,
                title="Dominant session",
            )
            target = _write_capture(
                root / "target.zip",
                "session-target",
                [
                    _message("t1", "IDE tooling with ruff", 1.0, role="user"),
                    _message("t2", "IDE layer with shellcheck", 2.0, role="assistant"),
                ],
                complete=True,
                title="Dev Kit",
            )
            ingest_artifact(dominant, corpus)
            ingest_artifact(target, corpus)

            rows = search_corpus(
                corpus, "lightweight IDE", ["dialogue", "evidence"], 8, recall=True
            )

            self.assertEqual(rows[0]["session_id"], "session-dominant")
            self.assertIn("session-target", {row["session_id"] for row in rows})

    def test_recall_ranks_complete_lexical_coverage_before_dialogue_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            dialogue = _write_capture(
                root / "dialogue.zip",
                "session-dialogue",
                [_message("d1", "architecture discussion", 1.0, role="assistant")],
                complete=True,
                title="Partial dialogue",
            )
            evidence = _write_capture(
                root / "evidence.zip",
                "session-evidence",
                [_message("e1", "architecture decision", 1.0, role="tool")],
                complete=True,
                title="Complete evidence",
            )
            ingest_artifact(dialogue, corpus)
            ingest_artifact(evidence, corpus)

            rows = search_corpus(
                corpus, "architecture decision", ["dialogue", "evidence"], 8, recall=True
            )

            self.assertTrue(rows)
            self.assertEqual(rows[0]["session_id"], "session-evidence")

    def test_recall_rerank_normalizes_diacritics_and_hyphen_like_fts5(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            target = _write_capture(
                root / "target.zip",
                "session-target",
                [_message("t1", "cafe foo bar deployment", 1.0, role="assistant")],
                complete=True,
                title="Normalized target",
            )
            distractor = _write_capture(
                root / "distractor.zip",
                "session-distractor",
                [_message("d1", "café unrelated", 1.0, role="assistant")],
                complete=True,
                title="Partial match",
            )
            ingest_artifact(target, corpus)
            ingest_artifact(distractor, corpus)

            rows = search_corpus(
                corpus, "café foo-bar", ["dialogue", "evidence"], 8, recall=True
            )

            self.assertTrue(rows)
            self.assertEqual(rows[0]["session_id"], "session-target")

    def test_strict_search_preserves_unicode_tokens_that_casefold_expands(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            capture = _write_capture(
                root / "unicode.zip",
                "session-unicode",
                [_message("u1", "Straße deployment note", 1.0, role="assistant")],
                complete=True,
                title="Unicode",
            )
            ingest_artifact(capture, corpus)

            rows = search_corpus(corpus, "Straße", ["dialogue", "evidence"], 8)

            self.assertEqual([row["session_id"] for row in rows], ["session-unicode"])

    def test_explicit_corpus_overrides_environment_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            explicit = build_two_session_corpus(root / "explicit", "explicit phrase")
            env_default = build_two_session_corpus(root / "environment", "environment phrase")
            env = os.environ.copy()
            env["SESSION_SEARCH_CORPUS"] = str(env_default)
            result = subprocess.run(
                [
                    "python3",
                    "-m",
                    "session_search.search",
                    "explicit phrase",
                    "--corpus",
                    str(explicit),
                    "--json",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = json.loads(result.stdout)
            self.assertTrue(rows)
            self.assertTrue(all("explicit phrase" in row["text"] for row in rows))

    def test_cli_recall_flag_uses_corpus_recall_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"
            target = _write_capture(
                root / "target.zip",
                "session-target",
                [_message("t1", "lightweight IDE layer", 1.0, role="assistant")],
                complete=True,
                title="Dev Kit",
            )
            distractor = _write_capture(
                root / "distractor.zip",
                "session-distractor",
                [_message("d1", "lightweight IDE reference output", 1.0, role="tool")],
                complete=True,
                title="Tool dump",
            )
            ingest_artifact(target, corpus)
            ingest_artifact(distractor, corpus)
            env = os.environ.copy()
            env["SESSION_SEARCH_CORPUS"] = str(corpus)

            result = subprocess.run(
                [
                    "python3",
                    "-m",
                    "session_search.search",
                    "lightweight IDE",
                    "--recall",
                    "--json",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            rows = json.loads(result.stdout)
            self.assertTrue(rows)
            self.assertEqual(rows[0]["session_id"], "session-target")

    def test_environment_default_enables_no_path_cli(self):
        with tempfile.TemporaryDirectory() as td:
            corpus = build_two_session_corpus(pathlib.Path(td), "environment phrase")
            env = os.environ.copy()
            env["SESSION_SEARCH_CORPUS"] = str(corpus)
            result = subprocess.run(
                ["python3", "-m", "session_search.search", "environment phrase", "--json"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = json.loads(result.stdout)
            self.assertEqual({row["session_id"] for row in rows}, {"session-a", "session-b"})

    def test_legacy_db_search_still_returns_existing_shape(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            capture = root / "legacy.zip"
            db = root / "legacy.sqlite3"
            synthetic_export(capture)
            import_export(capture, db)
            rows = search(str(db), "copper kettle", ["dialogue", "evidence"], 8)
            self.assertTrue(rows)
            self.assertEqual(
                set(rows[0]),
                {"ordinal", "message_id", "role", "content_type", "search_class", "text", "score"},
            )


if __name__ == "__main__":
    unittest.main()
