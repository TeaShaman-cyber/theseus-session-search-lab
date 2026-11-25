"""Reference experiments only: reuse canonical bundles and accepted-source recovery."""
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import test_chatgpt_export as fixtures
from session_search.accepted_membership import capture_accepted_membership, rederive_session_delta
from session_search.chatgpt_export import ingest_export
from session_search.corpus_store import semantic_snapshot
from session_search.shard_allocation import allocate_sessions, FIXED_OVERHEAD_BYTES
from session_search.shard_bundle import build_bundle, verify_bundle


def placement_reference(bundles, observed_catalog, expected_set):
    """Test oracle, not a trust or publication boundary.

    Input files are immutable, synthetic bundles in the test's own custody.
    Catalog is a simulated exact provider-scoped runtime readback.
    """
    placement = {}
    expected_catalog = {}
    for path in bundles:
        manifest = verify_bundle(path)
        if manifest["accepted_set_sha256"] != expected_set:
            raise ValueError("ACCEPTED_SET_MISMATCH")
        shard = manifest["shard_id"]
        if shard in expected_catalog:
            raise ValueError("DUPLICATE_SHARD")
        expected_catalog[shard] = manifest["generation_id"]
        with ZipFile(path) as zf:
            rows = list(csv.DictReader(io.StringIO(zf.read("sessions.csv").decode("utf-8"))))
        for row in rows:
            session = row["session_id"]
            if session in placement:
                raise ValueError("DUPLICATE_SESSION")
            placement[session] = (shard, manifest["generation_id"])
    if expected_catalog != observed_catalog:
        raise ValueError("CATALOG_MISMATCH")
    return placement


def fixture_allocation(rows):
    # Use actual allocator, just shrink target so tiny synthetic chats split.
    return allocate_sessions(rows, target_bytes=FIXED_OVERHEAD_BYTES + 40000)


class VerifiedBundleHandoffReferenceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.corpus = self.root / "corpus"
        self.fixture = fixtures.ChatGPTExportAdapterTest()
        allocator = patch("session_search.neon_materializer.allocate_sessions", fixture_allocation)
        allocator.start()
        self.addCleanup(allocator.stop)

    def ingest(self, conversations):
        return ingest_export(
            self.fixture.write_export(self.root, conversations),
            self.corpus, report_session_delta=True,
        )["session_delta"]

    def bundles(self, prefix):
        from session_search.corpus_store import CorpusPaths
        from session_search.shard_allocation import load_session_rows
        import sqlite3
        with sqlite3.connect(CorpusPaths.from_root(self.corpus).db) as con:
            allocation = fixture_allocation(load_session_rows(con))
        paths = []
        catalog = {}
        for shard in allocation["shards"]:
            sid = shard["shard_id"]
            path = self.root / (prefix + "-" + sid + ".zip")
            receipt = build_bundle(
                self.corpus, provider="chatgpt", shard_id=sid,
                output_path=path, require_verified=True,
            )
            self.assertEqual(verify_bundle(path), receipt)
            paths.append(path)
            catalog[sid] = receipt["generation_id"]
        self.assertGreaterEqual(len(paths), 2)
        return paths, catalog

    def test_recover_delta_and_bind_to_exact_promoted_bundles(self):
        f = self.fixture
        single = f.conversation(cid="changed")
        del single["mapping"]["a"]
        stable = f.conversation(cid="stable")
        self.ingest([single, stable])
        old = capture_accepted_membership(self.corpus, scope="synthetic")
        old_paths, old_catalog = self.bundles("before")
        old_placement = placement_reference(old_paths, old_catalog, old["accepted_set_sha256"])
        original = self.ingest([f.conversation(cid="changed"), stable, f.conversation(cid="new")])
        recovered = rederive_session_delta(self.corpus, old, scope="synthetic")
        for key in ("added", "changed", "removed"):
            self.assertEqual(original[key], recovered[key])
        new_paths, new_catalog = self.bundles("after")
        new_set = recovered["current_accepted_set_sha256"]
        # Import/READY is not serving cutover: old and mixed vectors must fail.
        with self.assertRaisesRegex(ValueError, "CATALOG_MISMATCH"):
            placement_reference(new_paths, old_catalog, new_set)
        mixed = dict(new_catalog)
        common_shard = next(iter(old_catalog.keys() & new_catalog.keys()))
        mixed[common_shard] = old_catalog[common_shard]
        self.assertNotEqual(mixed, new_catalog)
        with self.assertRaisesRegex(ValueError, "CATALOG_MISMATCH"):
            placement_reference(new_paths, mixed, new_set)
        with self.assertRaisesRegex(ValueError, "ACCEPTED_SET_MISMATCH"):
            placement_reference([old_paths[0], *new_paths[1:]], new_catalog, new_set)
        # Exact verified bundle rows, not a later allocation or search hits.
        placement = placement_reference(new_paths, new_catalog, new_set)
        self.assertEqual(set(placement), {row[0] for row in semantic_snapshot(self.corpus)["sessions"]})
        self.assertEqual(set(recovered["added"]), placement.keys() - old_placement.keys())
        self.assertEqual(set(recovered["removed"]), old_placement.keys() - placement.keys())
        selected = set(recovered["added"] + recovered["changed"])
        self.assertTrue(selected)
        self.assertTrue(selected.issubset(placement))
        self.assertFalse(any(s.startswith("stable") for s in selected))
        self.assertTrue(all(placement[s][1] == new_catalog[placement[s][0]] for s in selected))
        with self.assertRaisesRegex(ValueError, "CATALOG_MISMATCH"):
            placement_reference(new_paths[:-1], new_catalog, new_set)
        # The global accepted-set digest changes generation identity even
        # for stable conversations. A generation change is not content change.
        stable_ids = {s for s in placement if s.startswith("stable")}
        self.assertTrue(stable_ids)
        self.assertTrue(all(placement[s] != old_placement[s] for s in stable_ids))

    def test_repeat_export_changes_bundle_generation_without_analysis_work(self):
        f = self.fixture
        self.ingest([f.conversation(cid="one", current="b"), f.conversation(cid="two")])
        old = capture_accepted_membership(self.corpus, scope="synthetic")
        old_paths, old_catalog = self.bundles("before")
        old_placement = placement_reference(old_paths, old_catalog, old["accepted_set_sha256"])
        report = self.ingest([f.conversation(cid="one", current="a"), f.conversation(cid="two")])
        recovered = rederive_session_delta(self.corpus, old, scope="synthetic")
        self.assertNotEqual(old["accepted_set_sha256"], recovered["current_accepted_set_sha256"])
        new_paths, new_catalog = self.bundles("after")
        self.assertNotEqual(old_catalog, new_catalog)
        new_placement = placement_reference(new_paths, new_catalog, recovered["current_accepted_set_sha256"])
        self.assertEqual(old_placement.keys(), new_placement.keys())
        for key in ("added", "changed", "removed"):
            self.assertEqual(report[key], [])
            self.assertEqual(recovered[key], [])
        # This is content-dependent work only; provenance-sensitive work and
        # actual consumer completion must be evaluated independently.
