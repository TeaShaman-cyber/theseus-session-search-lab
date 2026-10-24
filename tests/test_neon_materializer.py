import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from session_search.neon_materializer import (
    build_source_manifest,
    load_source_rows,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


class NeonMaterializerSourceTest(unittest.TestCase):
    def make_corpus(self, root: Path):
        accepted = root / "ledger" / "accepted"
        accepted.mkdir(parents=True)
        for sha in (SHA_A, SHA_B):
            (accepted / f"{sha}.json").write_text(json.dumps({"artifact_sha256": sha}))

        db = root / "corpus.sqlite3"
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE sessions(
                session_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                coverage_state TEXT NOT NULL,
                coverage_reason TEXT NOT NULL,
                first_message_time REAL,
                last_message_time REAL,
                first_accepted_at TEXT,
                last_accepted_at TEXT,
                title_source_time REAL,
                title_source_artifact_sha256 TEXT
            );
            CREATE TABLE messages(
                row_id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                message_id TEXT,
                local_identity TEXT NOT NULL,
                canonical_message_sha256 TEXT NOT NULL,
                role TEXT NOT NULL,
                content_type TEXT NOT NULL,
                search_class TEXT NOT NULL,
                create_time REAL,
                provider_order INTEGER,
                text TEXT NOT NULL
            );
            CREATE TABLE artifacts(
                artifact_id INTEGER PRIMARY KEY,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                source_schema TEXT NOT NULL,
                source_adapter TEXT NOT NULL,
                original_filename TEXT,
                observed_title TEXT NOT NULL,
                accepted_at TEXT NOT NULL,
                coverage_state TEXT NOT NULL,
                session_id TEXT NOT NULL,
                ledger_sha256 TEXT NOT NULL,
                observed_min_time REAL,
                observed_max_time REAL,
                observed_message_count INTEGER NOT NULL
            );
        """)
        con.executemany(
            "INSERT INTO sessions(session_id,title,coverage_state,coverage_reason,first_message_time,last_message_time) VALUES (?,?,?,?,?,?)",
            [
                ("private-s1", "One", "COMPLETE_EXPOSED_CONVERSATION", "ok", 1.0, 2.0),
                ("private-s2", "Two", "PARTIAL_SESSION_SLICE", "partial", 3.0, 4.0),
            ],
        )
        con.executemany(
            "INSERT INTO messages(row_id,session_id,ordinal,message_id,local_identity,canonical_message_sha256,role,content_type,search_class,create_time,provider_order,text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, "private-s1", 0, "m1", "l1", SHA_A, "user", "text", "dialogue", 1.0, 0, "hello"),
                (2, "private-s1", 1, None, "fallback-2", SHA_B, "assistant", "text", "trace", 2.0, 1, "world"),
                (3, "private-s2", 0, "hidden-id", "hidden-local", SHA_A, "unknown", "text", "hidden", 3.0, 0, "secret"),
            ],
        )
        con.executemany(
            "INSERT INTO artifacts(artifact_id,sha256,size_bytes,source_schema,source_adapter,observed_title,accepted_at,coverage_state,session_id,ledger_sha256,observed_message_count) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (1, SHA_A, 1, "portable", "xai-export", "a", "2026-01-01T00:00:00+00:00", "COMPLETE_EXPOSED_CONVERSATION", "private-s1", SHA_A, 2),
                (2, SHA_B, 1, "portable", "xai-export", "b", "2026-01-02T00:00:00+00:00", "PARTIAL_SESSION_SLICE", "private-s2", SHA_B, 1),
            ],
        )
        con.commit()
        con.close()
        return root

    def test_manifest_is_deterministic_and_public_safe(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_corpus(Path(td))
            first = build_source_manifest(root, provider="xai", shard_id="xai-0001")
            second = build_source_manifest(root, provider="xai", shard_id="xai-0001")
            self.assertEqual(first, second)
            self.assertEqual(first["session_count"], 2)
            self.assertEqual(first["message_count"], 2)
            self.assertEqual(first["accepted_artifact_count"], 2)
            self.assertEqual(len(first["source_manifest_sha256"]), 64)
            self.assertEqual(len(first["generation_id"]), 64)
            payload = json.dumps(first, sort_keys=True)
            self.assertNotIn("private-s1", payload)
            self.assertNotIn("hello", payload)


    def test_manifest_capture_respects_corpus_mutation_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_corpus(Path(td) / "corpus")
            lock = root / "mutation.lock"
            lock.mkdir()
            with self.assertRaisesRegex(RuntimeError, "CORPUS_MUTATION_LOCKED"):
                build_source_manifest(root, provider="xai", shard_id="xai-0001")

    def test_hidden_rows_are_excluded_and_null_message_id_uses_local_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_corpus(Path(td))
            sessions, messages = load_source_rows(root, provider="xai")
            self.assertEqual(len(sessions), 2)
            self.assertEqual(len(messages), 2)
            self.assertEqual([row[2] for row in messages], ["m1", "fallback-2"])
            self.assertNotIn("hidden-id", [row[2] for row in messages])

    def test_provider_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_corpus(Path(td))
            con = sqlite3.connect(root / "corpus.sqlite3")
            con.execute("UPDATE artifacts SET source_adapter='deepseek-export' WHERE artifact_id=2")
            con.commit(); con.close()
            with self.assertRaisesRegex(ValueError, "SOURCE_PROVIDER_MISMATCH"):
                build_source_manifest(root, provider="xai", shard_id="xai-0001")

    def test_target_message_id_collision_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.make_corpus(Path(td))
            con = sqlite3.connect(root / "corpus.sqlite3")
            con.execute("UPDATE messages SET local_identity='m1' WHERE row_id=2")
            con.execute("UPDATE messages SET message_id=NULL WHERE row_id=2")
            con.commit(); con.close()
            with self.assertRaisesRegex(ValueError, "TARGET_MESSAGE_ID_COLLISION"):
                load_source_rows(root, provider="xai")


class NeonMaterializerCliTest(unittest.TestCase):
    def test_dry_run_works_without_pythonpath(self):
        root = Path(__file__).resolve().parents[1]
        helper = NeonMaterializerSourceTest()
        with tempfile.TemporaryDirectory() as td:
            corpus = helper.make_corpus(Path(td) / "corpus")
            env = os.environ.copy(); env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [sys.executable, "scripts/materialize_neon_shard.py", "--corpus", str(corpus), "--provider", "xai", "--shard-id", "xai-0001", "--dry-run"],
                cwd=root, env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["provider"], "xai")
            self.assertEqual(payload["message_count"], 2)
            self.assertNotIn("private-s1", result.stdout)

    def test_materializer_cannot_mark_ready_or_activate(self):
        root = Path(__file__).resolve().parents[1]
        module = (root / "session_search" / "neon_materializer.py").read_text()
        script = (root / "scripts" / "materialize_neon_shard.py").read_text()
        combined = module + script
        self.assertNotIn("mark_generation_ready", combined)
        self.assertNotIn("activate_generation", combined)


if __name__ == "__main__":
    unittest.main()
