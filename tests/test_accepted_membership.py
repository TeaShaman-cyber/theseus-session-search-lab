import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from session_search.artifact_overlap import set_digest
from session_search.corpus_store import CorpusPaths, ingest_artifact
from session_search.accepted_membership import capture_accepted_membership, compare_accepted_membership
from test_corpus_store import _write_capture, _message


class AcceptedMembershipTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.corpus = self.root / "corpus"

    def ingest(self, name, text):
        source = _write_capture(
            self.root / (name + ".zip"), name,
            [_message(name + "-m1", text, 1.0, role="user")],
        )
        ingest_artifact(source, self.corpus)
        return hashlib.sha256(source.read_bytes()).hexdigest()

    def capture(self, scope="fixture-corpus"):
        return capture_accepted_membership(self.corpus, scope=scope)

    def test_real_ingest_snapshot_and_delta(self):
        first_id = self.ingest("one", "synthetic first evidence")
        first = self.capture()
        self.assertEqual(first, self.capture())
        self.assertEqual(first["accepted_set_sha256"], set_digest([first_id]))
        self.assertEqual(first["currentness"], "UNKNOWN_WITHOUT_SOURCE_WATERMARK")
        self.assertEqual(first["verification_level"], "projection_consistency")
        self.assertNotIn("synthetic first evidence", json.dumps(first))
        self.assertNotIn(str(self.root), json.dumps(first))
        unchanged = compare_accepted_membership(first, self.capture())
        self.assertEqual(unchanged["status"], "UNCHANGED")
        self.assertEqual(unchanged["added_artifacts"], [])
        second_id = self.ingest("two", "synthetic second evidence")
        second = self.capture()
        delta = compare_accepted_membership(first, second)
        self.assertEqual(delta["status"], "ADDED")
        self.assertEqual(delta["added_artifacts"], [second_id])
        self.assertEqual(delta["currentness"], "UNKNOWN_WITHOUT_SOURCE_WATERMARK")
        self.assertEqual(delta["current_snapshot_sha256"], second["snapshot_sha256"])
        self.assertEqual(delta["previous_snapshot_sha256"], first["snapshot_sha256"])

    def test_reject_scope_removal_and_tampered_receipt(self):
        self.ingest("one", "first")
        first = self.capture()
        self.ingest("two", "second")
        second = self.capture()
        with self.assertRaisesRegex(ValueError, "SCOPE_MISMATCH"):
            compare_accepted_membership(first, self.capture("different"))
        with self.assertRaisesRegex(ValueError, "MEMBERSHIP_REMOVED"):
            compare_accepted_membership(second, first)
        for change in [
            lambda s: s.update(accepted_set_sha256="0" * 64),
            lambda s: s["members"].append(s["members"][0]),
            lambda s: s["members"][0].update(ledger_sha256="0" * 64),
            lambda s: s.update(currentness="CURRENT"),
            lambda s: s.update(extra=True),
        ]:
            bad = copy.deepcopy(second)
            change(bad)
            with self.assertRaises(ValueError):
                compare_accepted_membership(first, bad)

    def test_changed_ledger_identity_even_with_valid_receipt_is_rejected(self):
        self.ingest("one", "first")
        first = self.capture()
        changed = copy.deepcopy(first)
        changed["members"][0]["ledger_sha256"] = "f" * 64
        del changed["snapshot_sha256"]
        changed["snapshot_sha256"] = hashlib.sha256(
            json.dumps(changed, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "LEDGER_IDENTITY_CHANGED"):
            compare_accepted_membership(first, changed)

    def test_missing_locked_or_inconsistent_corpus_is_not_empty_snapshot(self):
        with self.assertRaisesRegex(ValueError, "CORPUS_NOT_INITIALIZED"):
            self.capture()
        self.assertFalse(self.corpus.exists())
        sha = self.ingest("one", "first")
        paths = CorpusPaths.from_root(self.corpus)
        paths.mutation_lock.mkdir()
        with self.assertRaisesRegex(RuntimeError, "CORPUS_MUTATION_LOCKED"):
            self.capture()
        paths.mutation_lock.rmdir()
        entry = paths.accepted_ledger / (sha + ".json")
        raw = json.loads(entry.read_text())
        raw["accepted_at"] = "modified"
        entry.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError, "PROJECTION_NOT_CONSISTENT"):
            self.capture()
        self.assertFalse(paths.mutation_lock.exists())

    def test_no_implicit_bootstrap_or_blank_scope(self):
        self.ingest("one", "first")
        with self.assertRaises(ValueError):
            self.capture(" ")
        with self.assertRaises(ValueError):
            compare_accepted_membership(None, self.capture())

    def test_new_artifact_is_not_a_claim_of_a_new_message(self):
        first_id = self.ingest("one", "same evidence")
        first = self.capture()
        source = _write_capture(
            self.root / "second-capture.zip", "one",
            [_message("one-m1", "same evidence", 1.0, role="user")],
            title="Different capture title",
        )
        ingest_artifact(source, self.corpus)
        second_id = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertNotEqual(first_id, second_id)
        delta = compare_accepted_membership(first, self.capture())
        self.assertEqual(delta["added_artifacts"], [second_id])
        self.assertNotIn("new_messages", delta)
