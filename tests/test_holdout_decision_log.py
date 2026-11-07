import importlib.util
import json
import pathlib
import sqlite3
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "012-independent-holdout" / "decision_log.py"


def load():
    spec = importlib.util.spec_from_file_location("holdout_decision_log", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HoldoutDecisionLogTest(unittest.TestCase):
    def _db(self, root):
        path = root / "c.sqlite3"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE messages(
              session_id text, ordinal integer, message_id text, role text,
              search_class text, create_time real, text text
            );
            INSERT INTO messages VALUES('old',1,'target','assistant','dialogue',1.0,'old target text');
            INSERT INTO messages VALUES('future',2,'future','assistant','dialogue',5.0,'future target text');
            """
        )
        conn.close()
        return path

    def test_accept_requires_target_before_query_and_stores_only_fingerprint(self):
        m = load()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            db = self._db(root)
            discovery = [{"session_id":"q","ordinal":9,"message_id":"q1","create_time":3.0,"text":"remember old target"}]
            decision = m.make_accept_decision(db, discovery, index=0, target_session_id="old", target_message_id="target", reason="specific prior fact")
            self.assertEqual(decision["index"], 0)
            self.assertEqual(decision["status"], "accept")
            self.assertIn("target_content_fp", decision)
            self.assertNotIn("target_text", decision)
            with self.assertRaisesRegex(ValueError, "TARGET_NOT_BEFORE_QUERY"):
                m.make_accept_decision(db, discovery, index=0, target_session_id="future", target_message_id="future", reason="bad")

    def test_conflicting_decision_fails_closed(self):
        m = load()
        current=[{"index":0,"status":"reject","reason":"ambiguous"}]
        with self.assertRaisesRegex(ValueError, "DECISION_CONFLICT"):
            m.upsert_decision(current, {"index":0,"status":"accept","reason":"changed","target_content_fp":"x"})

    def test_freeze_uses_first_ten_accepted_in_index_order(self):
        m = load()
        discovery=[
            {"session_id":"q","ordinal":i,"message_id":f"q{i}","create_time":float(i),"text":f"query {i}"}
            for i in range(20)
        ]
        decisions=[]
        for i in range(15):
            status="accept" if i % 2 == 0 else "reject"
            row={"index":i,"status":status,"reason":"r"}
            if status=="accept":
                row["target_content_fp"]=f"fp{i}"
            decisions.append(row)
        frozen=m.freeze_holdout(discovery,decisions,count=5)
        self.assertEqual([r["source_index"] for r in frozen],[0,2,4,6,8])
        self.assertEqual([r["case_id"] for r in frozen],["h203-001","h203-002","h203-003","h203-004","h203-005"])
        self.assertIn("query_text", frozen[0])
        self.assertNotIn("message_id", frozen[0])


if __name__ == "__main__":
    unittest.main()
