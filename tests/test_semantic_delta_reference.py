"""Issue 59: reference experiments, not an incremental projection implementation."""
import collections
import pathlib
import tempfile
import unittest

import test_chatgpt_export as fixtures
from session_search.artifact import normalize_artifact
from session_search.branch_routing import (
    build_official_families, plan_projection_routes, route_artifact,
)
from session_search.chatgpt_export import materialize_export
from session_search.corpus_store import (
    ingest_reconciled_many, rebuild_corpus, semantic_snapshot, verify_corpus,
)
from session_search.neon_materializer import build_source_manifest


def projected_conversations(snapshot):
    """Test oracle over the existing full semantic snapshot; omit custody fields."""
    result = collections.defaultdict(list)
    for row in snapshot["sessions"]:
        result[row[0]].append(("session", row[:6]))
    for row in snapshot["messages"]:
        result[row[0]].append(("message", row))
    return dict(result)


def scoped_route_candidate(before, additions):
    """Research-only candidate: reuse existing route decisions outside changed families."""
    old_families = build_official_families(before)
    new_families = build_official_families([*before, *additions])
    changed = {key for key in old_families.keys() | new_families.keys()
               if old_families.get(key) != new_families.get(key)}
    result = plan_projection_routes(before)
    rerouted = set()
    for artifact in before:
        family = (artifact.source_conversation_id if artifact.source_adapter == "chatgpt-export"
                  else artifact.session_id)
        if family in changed:
            result[artifact.artifact_sha256] = route_artifact(artifact, new_families)
            rerouted.add(artifact.artifact_sha256)
    for artifact in additions:
        result[artifact.artifact_sha256] = route_artifact(artifact, new_families)
    return result, changed, rerouted


class SemanticDeltaReferenceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.corpus = self.root / "corpus"
        self.fixture = fixtures.ChatGPTExportAdapterTest()

    def export(self, label, conversations):
        source = self.fixture.write_export(self.root, conversations)
        return materialize_export(source, self.root / label)

    def ingest(self, paths):
        self.assertEqual(ingest_reconciled_many(paths, self.corpus)["status"], "COMPLETE")
        self.assertEqual(verify_corpus(self.corpus)["status"], "VERIFIED")
        return semantic_snapshot(self.corpus)

    def check_rebuild(self, expected):
        rebuild_corpus(self.corpus)
        self.assertEqual(semantic_snapshot(self.corpus), expected)

    def test_repeated_export_changes_generation_not_projected_conversation(self):
        first = self.export("first", [self.fixture.conversation(cid="one", current="b"),
                                      self.fixture.conversation(cid="two")])
        second = self.export("second", [self.fixture.conversation(cid="one", current="a"),
                                        self.fixture.conversation(cid="two")])
        old = [normalize_artifact(p) for p in first]
        new = [normalize_artifact(p) for p in second]
        self.assertTrue({a.artifact_sha256 for a in old}.isdisjoint(a.artifact_sha256 for a in new))
        a = self.ingest(first)
        manifest_a = build_source_manifest(self.corpus, provider="chatgpt", shard_id="chatgpt-0001")
        b = self.ingest(second)
        manifest_b = build_source_manifest(self.corpus, provider="chatgpt", shard_id="chatgpt-0001")
        self.assertEqual(projected_conversations(a), projected_conversations(b))
        self.assertGreater(len(b["sources"]), len(a["sources"]))
        self.assertNotEqual(manifest_a["accepted_set_sha256"], manifest_b["accepted_set_sha256"])
        self.assertNotEqual(manifest_a["generation_id"], manifest_b["generation_id"])
        self.assertEqual(manifest_a["source_projection_sha256"], manifest_b["source_projection_sha256"])
        routes, families, old_rechecks = scoped_route_candidate(old, new)
        self.assertEqual(routes, plan_projection_routes(old + new))
        self.assertEqual(families, set())
        self.assertEqual(old_rechecks, set())
        self.check_rebuild(b)

    def test_new_branch_can_move_old_messages_and_only_affected_family_routes_need_recheck(self):
        f = self.fixture
        single = f.conversation(cid="changed")
        del single["mapping"]["a"]
        stable = f.conversation(cid="stable")
        first = self.export("first", [single, stable])
        raw = f.write_raw_capture(self.root / "raw.zip", "changed", [
            f.message("m-u", "user", "text", ["question"], 10.0),
            f.message("m-b", "assistant", "text", ["answer B"], 30.0),
            f.message("extra", "assistant", "text", ["older capture only"], 35.0),
        ])
        first.append(raw)
        second = self.export("second", [f.conversation(cid="changed"), stable,
                                        f.conversation(cid="new")])
        old = [normalize_artifact(p) for p in first]
        new = [normalize_artifact(p) for p in second]
        a = self.ingest(first)
        b = self.ingest(second)
        routes, families, old_rechecks = scoped_route_candidate(old, new)
        self.assertEqual(routes, plan_projection_routes(old + new))
        self.assertEqual(families, {"changed", "new"})
        self.assertEqual(old_rechecks, {x.artifact_sha256 for x in old
                                       if x.session_id == "changed"})
        raw_sha = normalize_artifact(raw).artifact_sha256
        old_route = plan_projection_routes(old)[raw_sha]
        new_route = routes[raw_sha]
        self.assertEqual(old_route.projected_session_id, "changed")
        self.assertEqual(new_route.state, "BRANCH_MATCH")
        self.assertTrue(new_route.projected_session_id.startswith("changed~branch-"))
        self.assertEqual({r[0] for r in b["messages"] if r[2] == "extra"}, {new_route.projected_session_id})
        before, after = projected_conversations(a), projected_conversations(b)
        actual_changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
        self.assertIn("changed", actual_changed)
        self.assertIn(new_route.projected_session_id, actual_changed)
        self.assertFalse(any(key.startswith("stable") for key in actual_changed))
        self.assertTrue(all(key.startswith(("changed", "new")) for key in actual_changed))
        self.check_rebuild(b)

    def test_routing_equivalence_is_not_analysis_equivalence(self):
        f = self.fixture
        first = self.export("first", [f.conversation(cid="one"), f.conversation(cid="missing")])
        corrected = f.conversation(cid="one")
        corrected["mapping"]["u"]["message"]["create_time"] = 5.0
        second = self.export("second", [corrected])
        old = [normalize_artifact(p) for p in first]
        new = [normalize_artifact(p) for p in second]
        a = self.ingest(first)
        b = self.ingest(second)
        routes, families, old_rechecks = scoped_route_candidate(old, new)
        self.assertEqual(routes, plan_projection_routes(old + new))
        self.assertEqual(families, set())
        self.assertEqual(old_rechecks, set())
        before, after = projected_conversations(a), projected_conversations(b)
        self.assertEqual(set(before), set(after))
        changed = {key for key in before if before[key] != after[key]}
        self.assertEqual(changed, {key for key in before if key == "one" or key.startswith("one~branch-")})
        self.assertEqual({row[8] for row in b["messages"]
                          if row[0] in changed and row[2] == "m-u"}, {5.0})
        # A conversation missing from the latest export is still accepted history.
        self.assertTrue(any(key.startswith("missing") for key in after))
        self.assertEqual({key: value for key, value in before.items() if key.startswith("missing")},
                         {key: value for key, value in after.items() if key.startswith("missing")})
        self.check_rebuild(b)
