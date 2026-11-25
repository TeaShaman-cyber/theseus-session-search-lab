import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from session_search.corpus_store import ingest_artifact, rebuild_corpus
from session_search.session_evidence_delta import capture_session_evidence, compare_session_evidence
from test_corpus_store import _write_capture, _message


class SessionEvidenceDeltaTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.corpus = self.root / "corpus"

    def ingest(self, name, messages, session="one", complete=False):
        path = _write_capture(self.root / (name + ".zip"), session, messages,
                              title=name, complete=complete)
        ingest_artifact(path, self.corpus)

    def capture(self, session="one", **limits):
        return capture_session_evidence(
            self.corpus, scope="fixture", session_id=session,
            max_events=limits.get("max_events", 20), max_sources=limits.get("max_sources", 50),
        )

    def test_recapture_is_provenance_only_and_equal_text_distinct_id_is_new(self):
        message = _message("m1", "same text", 1.0, role="user")
        self.ingest("first", [message])
        before = self.capture()
        self.ingest("repeat", [message])
        repeated = self.capture()
        delta = compare_session_evidence(before, repeated)
        self.assertEqual(delta["added"], [])
        self.assertEqual(delta["changed"], [])
        self.assertEqual(delta["provenance_only"], ["id:m1"])
        self.ingest("second", [message, _message("m2", "same text", 2.0, role="user")])
        after = self.capture()
        delta = compare_session_evidence(repeated, after)
        self.assertEqual(delta["added"], ["id:m2"])
        self.assertEqual(delta["provenance_only"], ["id:m1"])
        self.assertNotIn("same text", str(after))
        self.assertEqual(after["currentness"], "UNKNOWN_WITHOUT_SOURCE_WATERMARK")

    def test_unrelated_session_does_not_change_event_fingerprint(self):
        self.ingest("first", [_message("m1", "one", 1.0)])
        first = self.capture()
        self.ingest("other", [_message("m1", "one", 1.0)], session="other")
        second = self.capture()
        self.assertNotEqual(first["accepted_membership"]["snapshot_sha256"], second["accepted_membership"]["snapshot_sha256"])
        self.assertEqual(first["events"], second["events"])
        delta = compare_session_evidence(first, second)
        self.assertEqual(delta["added"], [])
        self.assertEqual(delta["changed"], [])
        self.assertEqual(delta["provenance_only"], [])
        with self.assertRaisesRegex(ValueError, "SESSION_SCOPE_MISMATCH"):
            compare_session_evidence(first, self.capture("other"))

    def test_changed_time_coverage_and_rebuild(self):
        self.ingest("first", [_message("m1", "one", 3.0)])
        first = self.capture()
        self.ingest("second", [_message("m1", "one", 1.0)], complete=True)
        second = self.capture()
        delta = compare_session_evidence(first, second)
        self.assertEqual(delta["changed"], ["id:m1"])
        self.assertTrue(delta["coverage_changed"])
        rebuild_corpus(self.corpus)
        self.assertEqual(second, self.capture())

    def test_limits_missing_scope_and_tampered_snapshot(self):
        self.ingest("first", [_message("m1", "one", 1.0), _message("m2", "two", 2.0)])
        with self.assertRaisesRegex(ValueError, "EVENT_LIMIT"):
            self.capture(max_events=1)
        with self.assertRaisesRegex(ValueError, "SOURCE_LIMIT"):
            self.capture(max_sources=1)
        with self.assertRaisesRegex(ValueError, "SESSION_NOT_FOUND"):
            self.capture("missing")
        good = self.capture()
        bad = copy.deepcopy(good)
        bad["events"][0]["evidence_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            compare_session_evidence(good, bad)

    def test_removed_event_reported_and_receipt_validation_is_strict(self):
        self.ingest("first", [_message("m1", "one", 1.0), _message("m2", "two", 2.0)])
        first = self.capture()

        def rehash(value):
            value.pop("snapshot_sha256")
            value["snapshot_sha256"] = hashlib.sha256(json.dumps(
                value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            return value

        # Synthetic projection-change receipt, not a claim that ingestion deletes events.
        removed = copy.deepcopy(first)
        removed["events"].pop()
        delta = compare_session_evidence(first, rehash(removed))
        self.assertEqual(delta["removed"], ["id:m2"])
        self.assertEqual(delta["added"], [])
        for change in [
            lambda v: v["events"].reverse(),
            lambda v: v["events"].append(v["events"][0]),
            lambda v: v["events"][0]["sources"].clear(),
            lambda v: v["events"][0]["sources"][0].update(sha256="0" * 64),
            lambda v: v["events"][0]["sources"][0].update(page_position=True),
            lambda v: v.update(currentness="CURRENT"),
        ]:
            bad = copy.deepcopy(first)
            change(bad)
            with self.assertRaises(ValueError):
                compare_session_evidence(first, rehash(bad))
        for limit in [0, True, 100001]:
            with self.assertRaisesRegex(ValueError, "INVALID_EVIDENCE_LIMIT"):
                self.capture(max_events=limit)
