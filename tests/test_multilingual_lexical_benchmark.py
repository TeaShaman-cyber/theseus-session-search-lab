import importlib.util
import pathlib
import sqlite3
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "014-multilingual-lexical-seam" / "benchmark.py"


def load():
    spec = importlib.util.spec_from_file_location("multilingual_lexical_benchmark", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MultilingualLexicalBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE docs(
              row_id INTEGER PRIMARY KEY,
              session_id TEXT NOT NULL,
              ordinal INTEGER NOT NULL,
              search_class TEXT NOT NULL,
              text_fp TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE fts_surface USING fts5(tokens, content='');
            CREATE VIRTUAL TABLE fts_stem USING fts5(tokens, content='');
            """
        )
        self.conn.execute("INSERT INTO docs VALUES(1,'s1',1,'dialogue','target')")
        self.conn.execute("INSERT INTO docs VALUES(2,'s2',1,'dialogue','other')")
        self.conn.execute("INSERT INTO fts_surface(rowid,tokens) VALUES(1,'восстановление корпуса')")
        self.conn.execute("INSERT INTO fts_surface(rowid,tokens) VALUES(2,'восстановить файлы')")
        self.conn.execute("INSERT INTO fts_stem(rowid,tokens) VALUES(1,'восстанов корпус')")
        self.conn.execute("INSERT INTO fts_stem(rowid,tokens) VALUES(2,'восстанов файл')")

    def tearDown(self):
        self.conn.close()

    def test_shared_stem_can_recover_target_that_surface_form_misses(self):
        surface = self.m.candidate_rows(
            self.conn, table="fts_surface", query_tokens=["восстановить"],
            per_session_cap=3, candidate_budget=30,
        )
        stem = self.m.candidate_rows(
            self.conn, table="fts_stem", query_tokens=["восстанов"],
            per_session_cap=3, candidate_budget=30,
        )
        self.assertIsNone(self.m.target_rank(surface, "target"))
        self.assertEqual(self.m.target_rank(stem, "target"), 1)

    def test_per_session_cap_is_applied_before_global_budget(self):
        self.conn.execute("INSERT INTO docs VALUES(3,'s1',2,'dialogue','x3')")
        self.conn.execute("INSERT INTO docs VALUES(4,'s1',3,'dialogue','x4')")
        self.conn.execute("INSERT INTO fts_stem(rowid,tokens) VALUES(3,'восстанов')")
        self.conn.execute("INSERT INTO fts_stem(rowid,tokens) VALUES(4,'восстанов')")
        rows = self.m.candidate_rows(
            self.conn, table="fts_stem", query_tokens=["восстанов"],
            per_session_cap=1, candidate_budget=30,
        )
        self.assertEqual(sum(r["session_id"] == "s1" for r in rows), 1)

    def test_table_name_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "LEXICAL_TABLE_INVALID"):
            self.m.candidate_rows(
                self.conn, table="fts_stem; DROP TABLE docs", query_tokens=["x"],
                per_session_cap=3, candidate_budget=30,
            )


if __name__ == "__main__":
    unittest.main()
