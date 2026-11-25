import unittest
import hashlib

import test_refresh_handoff_candidate as handoff_fixture
import test_chatgpt_export as fixtures
from session_search.accepted_membership import capture_accepted_membership
from session_search.chatgpt_export import ingest_export
from session_search.neon_materializer import capture_source_evidence_index, build_source_manifest


class SourceEvidenceIndexTest(unittest.TestCase):
    def setUp(self):
        self.fixture = handoff_fixture.RefreshHandoffCandidateTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.candidate = self.fixture.prepare()

    def capture(self, item, **overrides):
        return capture_source_evidence_index(self.fixture.corpus, **{
            "provider": "chatgpt", "shard_id": item["shard_id"],
            "expected_generation_id": item["generation_id"],
            "expected_membership": self.candidate["current_membership"],
            "session_ids": [item["session_id"]], **overrides,
        })

    def test_exact_source_links_and_no_message_bodies(self):
        item = self.candidate["selected"][0]
        result = self.capture(item)
        self.assertEqual(result["generation_id"], item["generation_id"])
        self.assertEqual(result["membership_snapshot_sha256"], self.candidate["current_membership"]["snapshot_sha256"])
        self.assertEqual(result["sessions"][0]["session_id"], item["session_id"])
        members = {m["artifact_sha256"] for m in self.candidate["current_membership"]["members"]}
        for message in result["sessions"][0]["messages"]:
            self.assertNotIn("text", message)
            self.assertTrue(message["sources"])
            self.assertTrue(all(src["artifact_sha256"] in members for src in message["sources"]))
            self.assertEqual(len(message["text_sha256"]), 64)
        self.assertNotIn("answer A", str(result))
        self.assertNotIn(str(self.fixture.root), str(result))

    def test_stale_membership_or_generation_and_wrong_selection_rejected(self):
        item = self.candidate["selected"][0]
        with self.assertRaisesRegex(ValueError, "MEMBERSHIP"):
            self.capture(item, expected_membership=self.candidate["previous_membership"])
        with self.assertRaisesRegex(ValueError, "GENERATION"):
            self.capture(item, expected_generation_id="0"*64)
        with self.assertRaisesRegex(ValueError, "SELECTION"):
            self.capture(item, session_ids=["missing"])
        with self.assertRaisesRegex(ValueError, "SELECTION"):
            self.capture(item, session_ids=[item["session_id"]]*2)

    def test_unrelated_refresh_preserves_scoped_content_identity(self):
        item = self.candidate["selected"][0]
        before = self.capture(item)
        fixture = fixtures.ChatGPTExportAdapterTest()
        ingest_export(fixture.write_export(self.fixture.root, [fixture.conversation(cid="unrelated")]), self.fixture.corpus)
        current = capture_accepted_membership(self.fixture.corpus, scope="synthetic:chatgpt")
        from session_search.corpus_store import CorpusPaths
        from session_search.shard_allocation import load_session_rows
        from test_verified_bundle_handoff_reference import fixture_allocation
        import sqlite3
        with sqlite3.connect(CorpusPaths.from_root(self.fixture.corpus).db) as con:
            shard = next(s for s in fixture_allocation(load_session_rows(con))["shards"] if item["session_id"] in s["session_ids"])
        manifest = build_source_manifest(self.fixture.corpus, provider="chatgpt", shard_id=shard["shard_id"])
        after = self.capture(item, shard_id=shard["shard_id"], expected_generation_id=manifest["generation_id"], expected_membership=current)
        self.assertNotEqual(before["generation_id"], after["generation_id"])
        self.assertNotEqual(before["membership_snapshot_sha256"], after["membership_snapshot_sha256"])
        self.assertEqual(before["sessions"][0]["content_sha256"], after["sessions"][0]["content_sha256"])

    def test_metadata_matches_exact_verified_bundle_rows(self):
        from session_search_arcade_materializer.bundle_consumer import verify_bundle, iter_bundle_messages
        for item in self.candidate["selected"]:
            path = self.fixture.paths[item["shard_id"]]
            receipt = verify_bundle(path, expected_bundle_sha256=item["bundle_sha256"])
            index = self.capture(item)
            self.assertEqual(index["source_manifest_sha256"], receipt["source_manifest_sha256"])
            actual = index["sessions"][0]["messages"]
            expected = [
                (ordinal, mid, role, cls, created, hashlib.sha256(text.encode("utf-8")).hexdigest())
                for sid, ordinal, mid, role, cls, created, text in iter_bundle_messages(path)
                if sid == item["session_id"]
            ]
            self.assertTrue(expected)
            self.assertEqual([
                tuple(m[k] for k in ("ordinal", "message_id", "role", "search_class", "create_time", "text_sha256"))
                for m in actual
            ], expected)
