import copy
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from session_search.feedback_inbox import read_feedback_records, submit_feedback
from session_search.feedback_neon import (
    decode_remote_records,
    PsqlReplica,
    plan_reconciliation,
    reconcile_feedback,
    sql_for_import,
)


def make_request(c):
    return {
        "schema": "theseus.session-search-feedback-request.v0",
        "baseline_request_ref": c * 32,
        "result_request_ref": None,
        "failure_class": "no_signal",
        "operator": {"name": "strict_to_recall", "version": "v1"},
        "outcome": "unknown",
        "notes": "PRIVATE local episode, never print",
    }


def record(c):
    with tempfile.TemporaryDirectory() as td:
        inbox = pathlib.Path(td)
        submit_feedback(inbox, make_request(c), received_at="2026-11-08T11:00:00Z")
        return read_feedback_records(inbox)[0]


class FeedbackNeonContractTest(unittest.TestCase):
    def test_strict_remote_decode_and_bounded_count(self):
        import base64
        from session_search.feedback_neon import encode_record
        row = record("a")
        exported = encode_record(row) + "\n"
        self.assertEqual(decode_remote_records(exported), [row])
        self.assertRaisesRegex(ValueError, "FEEDBACK_REPLICA_DUPLICATE", decode_remote_records, exported * 2)
        self.assertRaisesRegex(ValueError, "FEEDBACK_REPLICA_ROWS_EXCEEDED", decode_remote_records, exported * 1001)
        bad = copy.deepcopy(row)
        bad["feedback"]["outcome"] = "improved"
        raw = base64.b64encode(json.dumps(bad).encode()).decode()
        self.assertRaisesRegex(ValueError, "FEEDBACK_RECORD_REF_MISMATCH", decode_remote_records, raw)
        self.assertRaisesRegex(ValueError, "FEEDBACK_REPLICA_INVALID_BASE64", decode_remote_records, "!!!")

    def test_plan_rejects_same_key_different_timestamp(self):
        a = record("a")
        b = copy.deepcopy(a)
        b["received_at"] = "2026-11-08T12:00:00Z"
        with self.assertRaisesRegex(ValueError, "FEEDBACK_REPLICA_CONFLICT"):
            plan_reconciliation([a], [b])
        self.assertEqual(plan_reconciliation([a], [a]), ([], []))

    def test_sql_import_is_base64_only_and_rolls_back_conflicts(self):
        a = record("a")
        sql = sql_for_import([a])
        self.assertNotIn("PRIVATE local episode", sql)
        self.assertIn("ON CONFLICT (feedback_ref) DO NOTHING", sql)
        self.assertIn("FEEDBACK_REPLICA_CONFLICT", sql)
        self.assertIn("BEGIN;", sql)
        self.assertIn("COMMIT;", sql)
        self.assertIn("COPY", sql)

    def test_sync_replays_idempotently_without_losing_remote_records(self):
        a = record("a")
        b = record("b")
        with tempfile.TemporaryDirectory() as td:
            inbox = pathlib.Path(td) / "inbox"
            submit_feedback(inbox, make_request("a"), received_at=a["received_at"])
            remote = [b]

            def remote_get():
                return copy.deepcopy(remote)

            def remote_add(items):
                for row in items:
                    old = next((x for x in remote if x["feedback_ref"] == row["feedback_ref"]), None)
                    if old is not None and old != row:
                        raise ValueError("FEEDBACK_REPLICA_CONFLICT")
                    if old is None:
                        remote.append(copy.deepcopy(row))

            state = reconcile_feedback(inbox, remote_get, remote_add)
            self.assertEqual(state["uploaded"], 1)
            self.assertEqual(state["downloaded"], 1)
            self.assertEqual(read_feedback_records(inbox), sorted(remote, key=lambda x: x["feedback_ref"]))
            self.assertEqual(reconcile_feedback(inbox, remote_get, remote_add)["uploaded"], 0)
            self.assertEqual(reconcile_feedback(inbox, remote_get, remote_add)["downloaded"], 0)

    def test_psql_errors_never_expose_secret_or_private_notes(self):
        with patch("session_search.feedback_neon.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                [], 3, "", "FAILED: sensitive notes and credential"
            )
            with self.assertRaisesRegex(RuntimeError, "^FEEDBACK_REPLICA_SQL_FAILED$"):
                PsqlReplica._psql("SELECT 1;")

    def test_connection_must_match_explicit_database(self):
        with patch.object(PsqlReplica, "_psql", return_value="wrongdb\\n"):
            with self.assertRaisesRegex(RuntimeError, "FEEDBACK_REPLICA_DATABASE_MISMATCH"):
                PsqlReplica("neondb").read()

    def test_remote_write_failure_does_not_change_local(self):
        a = record("a")
        with tempfile.TemporaryDirectory() as td:
            inbox = pathlib.Path(td) / "inbox"
            submit_feedback(inbox, make_request("a"), received_at=a["received_at"])
            def fail(_):
                raise RuntimeError("remote refused")
            with self.assertRaisesRegex(RuntimeError, "remote refused"):
                reconcile_feedback(inbox, lambda: [], fail)
            self.assertEqual(len(read_feedback_records(inbox)), 1)


if __name__ == "__main__":
    unittest.main()
