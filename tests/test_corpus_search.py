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
