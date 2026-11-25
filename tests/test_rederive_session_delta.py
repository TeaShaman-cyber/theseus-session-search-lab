import copy
import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest

from session_search.accepted_membership import capture_accepted_membership, rederive_session_delta
from session_search.chatgpt_export import ingest_export
from session_search.corpus_store import CorpusPaths, semantic_snapshot, read_accepted_ledger
import test_chatgpt_export as fixtures


class RederiveSessionDeltaTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        self.corpus = self.root / "corpus"
        self.fixture = fixtures.ChatGPTExportAdapterTest()
        initial = self.fixture.conversation(cid="one")
        del initial["mapping"]["a"]
        self.import_conversations([initial, self.fixture.conversation(cid="stable")])
        self.previous = capture_accepted_membership(self.corpus, scope="fixture")
        self.expected = self.import_conversations([
            self.fixture.conversation(cid="one"),
            self.fixture.conversation(cid="stable"),
            self.fixture.conversation(cid="new"),
        ])["session_delta"]

    def import_conversations(self, conversations):
        source = self.fixture.write_export(self.root, conversations)
        return ingest_export(source, self.corpus, report_session_delta=True)

    def test_reconstruct_lost_report_and_leave_corpus_unchanged(self):
        paths = CorpusPaths.from_root(self.corpus)
        before = semantic_snapshot(self.corpus)
        ledger = read_accepted_ledger(paths)
        result = rederive_session_delta(self.corpus, self.previous, scope="fixture")
        self.assertEqual(result["status"], "REDERIVED")
        self.assertEqual(result["current_membership"], capture_accepted_membership(self.corpus, scope="fixture"))
        self.assertEqual(result["baseline_verification"], "rederived_under_current_contract")
        self.assertEqual(result["previous_accepted_set_sha256"], self.previous["accepted_set_sha256"])
        for key in ("added", "changed", "removed", "current_accepted_set_sha256"):
            self.assertEqual(result[key], self.expected[key])
        self.assertEqual(result["currentness"], "UNKNOWN_WITHOUT_SOURCE_WATERMARK")
        self.assertEqual(semantic_snapshot(self.corpus), before)
        self.assertEqual(read_accepted_ledger(paths), ledger)
        self.assertFalse(paths.mutation_lock.exists())
        self.assertFalse(list(paths.staging.glob("session-delta-*")))
        self.assertNotIn("answer A", json.dumps(result))

    def test_same_membership_and_wrong_scope(self):
        current = capture_accepted_membership(self.corpus, scope="fixture")
        result = rederive_session_delta(self.corpus, current, scope="fixture")
        for key in ("added", "changed", "removed"):
            self.assertEqual(result[key], [])
        with self.assertRaisesRegex(ValueError, "SCOPE_MISMATCH"):
            rederive_session_delta(self.corpus, self.previous, scope="other")

    def test_tampered_and_nonmember_receipts_rejected(self):
        for field in ("ledger_sha256", "artifact_sha256"):
            bad = copy.deepcopy(self.previous)
            bad["members"][0][field] = "f" * 64
            bad["members"].sort(key=lambda row: row["artifact_sha256"])
            from session_search.artifact_overlap import set_digest
            bad["accepted_set_sha256"] = set_digest(row["artifact_sha256"] for row in bad["members"])
            del bad["snapshot_sha256"]
            bad["snapshot_sha256"] = hashlib.sha256(json.dumps(
                bad, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()).hexdigest()
            with self.assertRaises(ValueError):
                rederive_session_delta(self.corpus, bad, scope="fixture")

    def test_corrupt_projection_and_lock_never_report_success(self):
        paths = CorpusPaths.from_root(self.corpus)
        paths.mutation_lock.mkdir()
        with self.assertRaisesRegex(RuntimeError, "CORPUS_MUTATION_LOCKED"):
            rederive_session_delta(self.corpus, self.previous, scope="fixture")
        paths.mutation_lock.rmdir()
        with sqlite3.connect(paths.db) as conn:
            conn.execute("UPDATE messages SET text='not derived from accepted source'")
        with self.assertRaisesRegex(ValueError, "CURRENT_DERIVATION_UNVERIFIED"):
            rederive_session_delta(self.corpus, self.previous, scope="fixture")
        self.assertFalse(paths.mutation_lock.exists())
