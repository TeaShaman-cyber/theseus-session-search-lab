import importlib.util
import pathlib
import sqlite3
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "012-independent-holdout" / "target_lookup.py"


def load():
    spec = importlib.util.spec_from_file_location("holdout_target_lookup", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HoldoutTargetLookupTest(unittest.TestCase):
    def test_lookup_excludes_future_rows(self):
        m = load()
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE messages(
              session_id text, ordinal integer, message_id text, role text,
              search_class text, create_time real, text text
            );
            CREATE VIRTUAL TABLE messages_fts USING fts5(text, content='messages', content_rowid='rowid');
            INSERT INTO messages VALUES('a',1,'old','assistant','dialogue',1.0,'alpha needle target');
            INSERT INTO messages VALUES('b',2,'future','assistant','dialogue',3.0,'alpha needle future');
            INSERT INTO messages_fts(rowid,text) SELECT rowid,text FROM messages;
            """
        )
        rows = m.lookup(conn, query="alpha needle", before_time=2.0, limit=10)
        self.assertEqual([row["message_id"] for row in rows], ["old"])

    def test_lookup_requires_positive_limit(self):
        m = load()
        conn = sqlite3.connect(":memory:")
        with self.assertRaisesRegex(ValueError, "LIMIT_INVALID"):
            m.lookup(conn, query="x", before_time=1.0, limit=0)


if __name__ == "__main__":
    unittest.main()
