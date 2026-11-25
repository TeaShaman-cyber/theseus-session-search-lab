"""Actual source artifacts, simulated trusted promotion/readback."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

import test_chatgpt_export as fixtures
from test_verified_bundle_handoff_reference import fixture_allocation
from session_search.accepted_membership import capture_accepted_membership, rederive_session_delta
from session_search.chatgpt_export import ingest_export
from session_search.corpus_store import CorpusPaths
from session_search.shard_allocation import load_session_rows
from session_search.shard_bundle import build_bundle
from session_search.serving_promotion_readiness import evaluate_serving_promotion_readiness

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "arcade/session-search-materializer/src"))
from session_search_arcade_materializer.bundle_consumer import verify_bundle_session_selection
from session_search.refresh_handoff_candidate import prepare_recovery_handoff


class RefreshHandoffCandidateTest(unittest.TestCase):
    def setUp(self):
        import sqlite3
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.corpus = self.root / "corpus"
        fixture = fixtures.ChatGPTExportAdapterTest()
        allocator = patch("session_search.neon_materializer.allocate_sessions", fixture_allocation)
        allocator.start()
        self.addCleanup(allocator.stop)
        initial = fixture.conversation(cid="changed")
        del initial["mapping"]["a"]
        stable = fixture.conversation(cid="stable")
        ingest_export(fixture.write_export(self.root, [initial, stable]), self.corpus)
        self.previous = capture_accepted_membership(self.corpus, scope="synthetic:chatgpt")
        ingest_export(fixture.write_export(self.root, [fixture.conversation(cid="changed"), stable]), self.corpus)
        self.delta = rederive_session_delta(self.corpus, self.previous, scope="synthetic:chatgpt")
        with sqlite3.connect(CorpusPaths.from_root(self.corpus).db) as con:
            shards = fixture_allocation(load_session_rows(con))["shards"]
        self.paths = {}
        replacements = []
        for shard in shards:
            sid = shard["shard_id"]
            path = self.root / (sid + ".zip")
            receipt = build_bundle(self.corpus, provider="chatgpt", shard_id=sid, output_path=path, require_verified=True)
            self.paths[sid] = path
            replacements.append({
                **{k: receipt[k] for k in ("provider", "shard_id", "generation_id", "source_manifest_sha256",
                   "bundle_sha256", "accepted_set_sha256", "session_count", "message_count")},
                "state": "READY", "time_min": 1.0, "time_max": 2.0,
                "previous_generation_id": "c"*64, "source_verified": True,
                "bundle_readback_verified": True, "ready_readback_verified": True,
            })
        self.assertGreaterEqual(len(replacements), 2)
        self.promotion = evaluate_serving_promotion_readiness({
            "schema": "theseus.session-search-serving-promotion-readiness-input.v1",
            "source_commit": "b"*40, "source_watermarks": [],
            "replacement_shards": replacements,
            "unchanged_catalog": [{"provider": "xai", "shard_id": "xai-0001", "generation_id": "e"*64,
                                   "time_min": 1.0, "time_max": 2.0, "state": "READY"}],
        }, repo_state={"head": "b"*40, "branch": "fixture", "clean": True},
           qa_state={"status": "PASS", "exit_code": 0, "output_sha256": "9"*64})
        self.assertEqual(self.promotion["state"], "READY_FOR_RUNTIME_GATES")
        self.catalog = [{k: item[k] for k in ("provider", "shard_id", "generation_id", "state")}
                        for item in self.promotion["replacement_shards"] + self.promotion["unchanged_catalog"]]

    def prepare(self, **changes):
        return prepare_recovery_handoff(**{
            "provider": "chatgpt", "previous": self.previous, "delta": self.delta,
            "promotion": self.promotion, "catalog": self.catalog, "bundle_paths": self.paths,
            "verify_selection": verify_bundle_session_selection, **changes,
        })

    def test_exact_candidate_keeps_membership_and_removed_work_visible(self):
        result = self.prepare()
        self.assertEqual(result, self.prepare())
        self.assertEqual(result["status"], "HANDOFF_CANDIDATE")
        self.assertEqual(result["previous_membership"], self.previous)
        self.assertEqual(result["current_membership"], self.delta["current_membership"])
        self.assertEqual(result["currentness"], "UNKNOWN_WITHOUT_SOURCE_WATERMARK")
        self.assertEqual(result["removed"], self.delta["removed"])
        self.assertEqual({row["session_id"] for row in result["selected"]}, set(self.delta["added"] + self.delta["changed"]))
        self.assertTrue(all(row["generation_id"] == next(c["generation_id"] for c in self.catalog if c["shard_id"] == row["shard_id"]) for row in result["selected"]))
        self.assertFalse(result["checkpoint_advanced"])
        self.assertNotIn("answer A", str(result))
        self.assertNotIn(str(self.root), str(result))

    def test_stale_catalog_and_mismatched_bundle_fail_closed(self):
        for catalog in (self.catalog[:-1], self.catalog + [self.catalog[0]],
                        [{**c, "generation_id": "f"*64} if c["provider"] == "chatgpt" else c for c in self.catalog]):
            with self.assertRaisesRegex(ValueError, "CATALOG"):
                self.prepare(catalog=catalog)
        paths = dict(self.paths)
        keys = list(paths)
        paths[keys[0]] = paths[keys[1]]
        with self.assertRaisesRegex(ValueError, "BUNDLE_SHA256_MISMATCH"):
            self.prepare(bundle_paths=paths)

    def test_untrusted_or_incomplete_delta_and_placement_fail(self):
        delta = copy.deepcopy(self.delta)
        delta["previous_snapshot_sha256"] = "0"*64
        with self.assertRaisesRegex(ValueError, "DELTA"):
            self.prepare(delta=delta)
        delta = copy.deepcopy(self.delta)
        delta["added"] = sorted(delta["added"] + ["missing"])
        with self.assertRaisesRegex(ValueError, "PLACEMENT"):
            self.prepare(delta=delta)
        promotion = copy.deepcopy(self.promotion)
        promotion["unchanged_catalog"].append({"provider": "chatgpt", "shard_id": "chatgpt-9999",
                                               "generation_id": "f"*64, "state": "READY"})
        with self.assertRaisesRegex(ValueError, "FULL_PROVIDER"):
            self.prepare(promotion=promotion)

    def test_removed_session_cannot_still_be_served(self):
        delta = copy.deepcopy(self.delta)
        selected = delta["added"] + delta["changed"]
        self.assertTrue(selected)
        sid = selected[0]
        for key in ("added", "changed"):
            delta[key] = [item for item in delta[key] if item != sid]
        delta["removed"] = sorted(delta["removed"] + [sid])
        with self.assertRaisesRegex(ValueError, "PLACEMENT_CONFLICT"):
            self.prepare(delta=delta)

    def test_duplicate_placement_is_rejected_even_with_verified_receipts(self):
        wanted = self.delta["added"] + self.delta["changed"]
        self.assertTrue(wanted)
        def duplicate(path, **kwargs):
            receipt, _ = verify_bundle_session_selection(path, **kwargs)
            return receipt, (wanted[0],)
        with self.assertRaisesRegex(ValueError, "PLACEMENT_CONFLICT"):
            self.prepare(verify_selection=duplicate)

    def test_ready_receipt_without_catalog_or_with_old_membership_is_insufficient(self):
        with self.assertRaisesRegex(ValueError, "CATALOG_MISMATCH"):
            self.prepare(catalog=[])
        promotion = copy.deepcopy(self.promotion)
        promotion["replacement_shards"][0]["accepted_set_sha256"] = self.previous["accepted_set_sha256"]
        with self.assertRaisesRegex(ValueError, "BUNDLE_ACCEPTED_SET_MISMATCH"):
            self.prepare(promotion=promotion)
