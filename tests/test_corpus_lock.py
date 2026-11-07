import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from session_search.corpus_lock import pin_corpus, resolve_corpus_lock


class CorpusLockTest(unittest.TestCase):
    def _db(self, root: pathlib.Path, name: str, marker: str) -> pathlib.Path:
        db = root / name
        conn = sqlite3.connect(db)
        try:
            conn.execute("CREATE TABLE corpus_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO corpus_meta(key,value) VALUES ('schema_version','session-search-corpus-v2')"
            )
            conn.execute("CREATE TABLE marker(value TEXT NOT NULL)")
            conn.execute("INSERT INTO marker(value) VALUES (?)", [marker])
            conn.commit()
        finally:
            conn.close()
        return db

    def test_lock_rejects_different_sqlite_even_with_same_schema(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first = self._db(root, "first.sqlite3", "first")
            second = self._db(root, "second.sqlite3", "second")
            lock = root / "current-corpus.json"

            pin_corpus(first, lock)
            payload = json.loads(lock.read_text(encoding="utf-8"))
            payload["db_path"] = str(second)
            lock.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "CORPUS_LOCK_DIGEST_MISMATCH"):
                resolve_corpus_lock(lock)

    def test_repin_accepts_new_corpus_without_code_or_constant_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first = self._db(root, "first.sqlite3", "first")
            second = self._db(root, "second.sqlite3", "second")
            lock = root / "current-corpus.json"

            pin_corpus(first, lock)
            self.assertEqual(resolve_corpus_lock(lock), first.resolve())

            pin_corpus(second, lock)
            self.assertEqual(resolve_corpus_lock(lock), second.resolve())

    def test_decision_bearing_clis_use_corpus_lock_not_arbitrary_corpus(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        commands = [
            [root / "experiments/008-real-corpus-exact-grouping/benchmark.py", "--help"],
            [root / "experiments/009-mmr-diversity/benchmark.py", "--help"],
            [root / "experiments/011-local-candidate-policy/benchmark.py", "--help"],
            [root / "experiments/012-independent-holdout/discover.py", "--help"],
            [root / "experiments/012-independent-holdout/target_lookup.py", "--help"],
            [root / "experiments/012-independent-holdout/decision_log.py", "accept", "--help"],
            [root / "experiments/013-per-session-cap-curve/benchmark.py", "--help"],
        ]
        for command in commands:
            env = dict(os.environ)
            env["PYTHONPATH"] = str(root)
            result = subprocess.run(
                [sys.executable, *map(str, command)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--corpus-lock", result.stdout, command[0])
            self.assertNotIn("--corpus CORPUS", result.stdout, command[0])

    def test_lock_fails_closed_on_schema_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            db = root / "bad.sqlite3"
            conn = sqlite3.connect(db)
            try:
                conn.execute("CREATE TABLE corpus_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute(
                    "INSERT INTO corpus_meta(key,value) VALUES ('schema_version','other-schema')"
                )
                conn.commit()
            finally:
                conn.close()
            lock = root / "current-corpus.json"

            with self.assertRaisesRegex(ValueError, "CORPUS_SCHEMA_MISMATCH"):
                pin_corpus(db, lock)


if __name__ == "__main__":
    unittest.main()
