import json
import os
import random
import subprocess
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

from session_search.shard_allocation import (
    HARD_LIMIT_BYTES,
    FIXED_OVERHEAD_BYTES,
    allocate_sessions,
    estimate_session_bytes,
    load_session_rows,
    provider_from_adapters,
    public_summary,
)


class ProviderLineageTest(unittest.TestCase):
    def test_known_provider_with_unknown_adapter_inherits_known_provider(self):
        self.assertEqual(
            provider_from_adapters({"chatgpt-export", "third-party-dialogue-bridge"}),
            "chatgpt",
        )

    def test_unknown_only_is_unresolved(self):
        self.assertIsNone(provider_from_adapters({"third-party-dialogue-bridge"}))

    def test_conflicting_known_providers_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "PROVIDER_CONFLICT"):
            provider_from_adapters({"chatgpt-export", "deepseek-export"})


class AllocationTest(unittest.TestCase):
    @staticmethod
    def row(session_id, accepted, provider, messages, text_bytes):
        return {
            "session_id": session_id,
            "first_accepted_at": accepted,
            "provider": provider,
            "searchable_messages": messages,
            "searchable_text_bytes": text_bytes,
            "adapters": (),
        }

    def test_identical_input_is_deterministic_independent_of_row_order(self):
        rows = [
            self.row("s3", "2026-01-03T00:00:00Z", "chatgpt", 2, 200),
            self.row("s1", "2026-01-01T00:00:00Z", "chatgpt", 2, 200),
            self.row("s2", "2026-01-02T00:00:00Z", "chatgpt", 2, 200),
            self.row("d1", "2026-01-01T00:00:00Z", "deepseek", 2, 200),
        ]
        target = FIXED_OVERHEAD_BYTES + estimate_session_bytes(2, 200) * 2
        a = allocate_sessions(rows, target_bytes=target)
        shuffled = list(rows)
        random.Random(42).shuffle(shuffled)
        b = allocate_sessions(shuffled, target_bytes=target)
        self.assertEqual(a, b)
        self.assertEqual([s["shard_id"] for s in a["shards"]], [
            "chatgpt-0001", "chatgpt-0002", "deepseek-0001"
        ])

    def test_exact_target_boundary_stays_in_one_shard(self):
        rows = [
            self.row("a", "2026-01-01T00:00:00Z", "chatgpt", 1, 100),
            self.row("b", "2026-01-02T00:00:00Z", "chatgpt", 1, 100),
        ]
        per = estimate_session_bytes(1, 100)
        result = allocate_sessions(rows, target_bytes=FIXED_OVERHEAD_BYTES + per * 2)
        self.assertEqual(len(result["shards"]), 1)
        self.assertEqual(result["shards"][0]["session_ids"], ["a", "b"])

    def test_atomic_session_may_exceed_target_but_not_hard_limit(self):
        row = self.row("large", "2026-01-01T00:00:00Z", "chatgpt", 100, 1000)
        est = estimate_session_bytes(100, 1000)
        result = allocate_sessions(
            [row],
            target_bytes=FIXED_OVERHEAD_BYTES + est - 1,
            hard_limit_bytes=FIXED_OVERHEAD_BYTES + est + 100,
        )
        self.assertTrue(result["shards"][0]["over_target"])

    def test_atomic_session_over_hard_limit_fails_closed(self):
        row = self.row("too-large", "2026-01-01T00:00:00Z", "chatgpt", 100, 1000)
        est = estimate_session_bytes(100, 1000)
        with self.assertRaisesRegex(ValueError, "SESSION_EXCEEDS_HARD_LIMIT"):
            allocate_sessions(
                [row],
                target_bytes=FIXED_OVERHEAD_BYTES + 100,
                hard_limit_bytes=FIXED_OVERHEAD_BYTES + est - 1,
            )


    def test_resolved_session_without_first_accepted_at_fails_closed(self):
        row = self.row("missing-time", None, "chatgpt", 1, 100)
        with self.assertRaisesRegex(ValueError, "FIRST_ACCEPTED_AT_MISSING"):
            allocate_sessions([row])

    def test_unresolved_provider_not_assigned_a_shard(self):
        row = self.row("u1", "2026-01-01T00:00:00Z", None, 3, 300)
        result = allocate_sessions([row])
        self.assertEqual(result["shards"], [])
        self.assertEqual([r["session_id"] for r in result["unresolved"]], ["u1"])

    def test_public_summary_contains_no_private_session_ids(self):
        rows = [self.row("private-session", "2026-01-01T00:00:00Z", "chatgpt", 3, 300)]
        summary = public_summary(allocate_sessions(rows))
        self.assertNotIn("private-session", repr(summary))
        self.assertEqual(summary["shards"][0]["sessions"], 1)


class SQLiteExtractionTest(unittest.TestCase):
    def test_provider_is_derived_from_all_routed_adapters(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "corpus.sqlite3"
            con = sqlite3.connect(db)
            con.executescript("""
                CREATE TABLE sessions(session_id TEXT PRIMARY KEY, first_accepted_at TEXT);
                CREATE TABLE messages(
                    session_id TEXT, search_class TEXT, text TEXT
                );
                CREATE TABLE artifacts(
                    artifact_id INTEGER PRIMARY KEY, source_adapter TEXT
                );
                CREATE TABLE artifact_routes(
                    artifact_id INTEGER, projected_session_id TEXT
                );
            """)
            con.executemany("INSERT INTO sessions VALUES (?,?)", [
                ("cg", "2026-01-01T00:00:00Z"),
                ("ds", "2026-01-02T00:00:00Z"),
                ("unknown", "2026-01-03T00:00:00Z"),
            ])
            con.executemany("INSERT INTO messages VALUES (?,?,?)", [
                ("cg", "dialogue", "hello"),
                ("cg", "trace", ""),
                ("cg", "hidden", "secret"),
                ("ds", "evidence", "deep"),
                ("unknown", "trace", "bridge"),
            ])
            con.executemany("INSERT INTO artifacts VALUES (?,?)", [
                (1, "chatgpt-export"),
                (2, "barn-doctor"),
                (3, "deepseek-export"),
                (4, "third-party-dialogue-bridge"),
            ])
            con.executemany("INSERT INTO artifact_routes VALUES (?,?)", [
                (1, "cg"), (2, "cg"), (3, "ds"), (4, "unknown")
            ])
            con.commit()
            rows = {r["session_id"]: r for r in load_session_rows(con)}
            self.assertEqual(rows["cg"]["provider"], "chatgpt")
            self.assertEqual(rows["cg"]["searchable_messages"], 2)
            self.assertEqual(rows["cg"]["searchable_text_bytes"], 5)
            self.assertEqual(rows["ds"]["provider"], "deepseek")
            self.assertIsNone(rows["unknown"]["provider"])
            con.close()



class PlannerCliTest(unittest.TestCase):
    @staticmethod
    def clean_env():
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        return env

    def test_help_works_without_pythonpath(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "scripts/plan_neon_shards.py", "--help"],
            cwd=root,
            env=self.clean_env(),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Plan provider-aware Neon shards", result.stdout)

    def test_synthetic_planner_run_works_without_pythonpath(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "corpus.sqlite3"
            con = sqlite3.connect(db)
            con.executescript("""
                CREATE TABLE sessions(session_id TEXT PRIMARY KEY, first_accepted_at TEXT);
                CREATE TABLE messages(session_id TEXT, search_class TEXT, text TEXT);
                CREATE TABLE artifacts(artifact_id INTEGER PRIMARY KEY, source_adapter TEXT);
                CREATE TABLE artifact_routes(artifact_id INTEGER, projected_session_id TEXT);
                INSERT INTO sessions VALUES ('s1','2026-01-01T00:00:00+00:00');
                INSERT INTO messages VALUES ('s1','dialogue','hello');
                INSERT INTO artifacts VALUES (1,'chatgpt-export');
                INSERT INTO artifact_routes VALUES (1,'s1');
            """)
            con.commit()
            con.close()
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/plan_neon_shards.py",
                    "--database",
                    str(db),
                ],
                cwd=root,
                env=self.clean_env(),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["shards"][0]["shard_id"], "chatgpt-0001")


if __name__ == "__main__":
    unittest.main()
