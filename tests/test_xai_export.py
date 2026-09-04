import hashlib
import json
import pathlib
import tempfile
import unittest
import zipfile

from session_search.artifact import normalize_artifact


class XaiExportAdapterTest(unittest.TestCase):
    def response(self, rid, parent, sender, message, ms, model="grok-4", children=None):
        return {
            "response": {
                "_id": rid,
                "conversation_id": "conv-active",
                "parent_response_id": parent,
                "children": children,
                "sender": sender,
                "message": message,
                "model": model,
                "create_time": {"$date": {"$numberLong": str(ms)}},
                "metadata": {},
            },
            "share_link": None,
        }

    def conversation(self, cid="conv-active", leaf="a2", responses=None, title="Grok notes"):
        responses = list(responses or [])
        for item in responses:
            item["response"]["conversation_id"] = cid
        return {
            "conversation": {
                "id": cid,
                "title": title,
                "create_time": {"$date": {"$numberLong": "1700000000000"}},
                "modify_time": {"$date": {"$numberLong": "1700000005000"}},
                "leaf_response_id": leaf,
            },
            "responses": responses,
        }

    def write_export(self, root: pathlib.Path, conversations):
        source = root / "xai-export.zip"
        payload = {"conversations": conversations, "media_posts": [], "projects": [], "tasks": []}
        with zipfile.ZipFile(source, "w") as zf:
            zf.writestr("ttl/30d/export_data/synthetic/prod-grok-backend.json", json.dumps(payload))
        return source

    def active_branch_conversation(self):
        # children is deliberately absent/incomplete; parent_response_id is authority.
        return self.conversation(responses=[
            self.response("u1", "external-anchor", "human", "shared question", 1700000001000, children=None),
            self.response("a1", "u1", "ASSISTANT", "abandoned answer", 1700000002000, children=None),
            self.response("a2", "u1", "assistant", "active answer", 1700000003000, children=None),
            self.response("m1", "a2", "grok-4-auto", "model-side trace", 1700000004000, model="grok-4-auto", children=None),
        ], leaf="m1")

    def test_explicit_leaf_selects_active_dialogue_and_preserves_off_path_as_trace(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            child = materialize_export(self.write_export(root, [self.active_branch_conversation()]), root / "out")[0]
            artifact = normalize_artifact(child)
            self.assertTrue(artifact.session_id.startswith("conv-active~branch-"))
            by_text = {m.text: m for m in artifact.messages}
            self.assertEqual(by_text["shared question"].search_class, "dialogue")
            self.assertEqual(by_text["active answer"].search_class, "dialogue")
            self.assertEqual(by_text["abandoned answer"].search_class, "trace")
            self.assertEqual(by_text["model-side trace"].search_class, "trace")
            self.assertEqual(by_text["shared question"].role, "user")
            self.assertEqual(by_text["active answer"].role, "assistant")

    def test_missing_active_leaf_with_multiple_leaves_materializes_explicit_branch_variants(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            c = self.conversation(cid="conv-branch", leaf=None, responses=[
                self.response("u", None, "human", "question", 1700000001000),
                self.response("a", "u", "assistant", "answer A", 1700000002000),
                self.response("b", "u", "assistant", "answer B", 1700000003000),
            ], title="Branched")
            source = self.write_export(root, [c])
            outputs = materialize_export(source, root / "out")
            self.assertEqual(len(outputs), 2)
            artifacts = [normalize_artifact(p) for p in outputs]
            self.assertEqual(len({a.session_id for a in artifacts}), 2)
            self.assertIn("conv-branch", {a.session_id for a in artifacts})
            self.assertEqual(sum("~branch-" in a.session_id for a in artifacts), 1)
            for a in artifacts:
                dialogue = {m.text for m in a.messages if m.search_class == "dialogue"}
                trace = {m.text for m in a.messages if m.search_class == "trace"}
                self.assertIn("question", dialogue)
                self.assertEqual(len({"answer A", "answer B"} & dialogue), 1)
                self.assertEqual(len({"answer A", "answer B"} & trace), 1)

    def test_unique_leaf_without_leaf_metadata_still_materializes_one_linear_session(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            c = self.conversation(cid="conv-linear", leaf=None, responses=[
                self.response("u", "external", "human", "hello", 1700000001000),
                self.response("a", "u", "assistant", "world", 1700000002000),
            ], title="Linear")
            outputs = materialize_export(self.write_export(root, [c]), root / "out")
            self.assertEqual(len(outputs), 1)
            artifact = normalize_artifact(outputs[0])
            self.assertEqual(artifact.session_id, "conv-linear")
            self.assertEqual([m.text for m in artifact.messages if m.search_class == "dialogue"], ["hello", "world"])

    def test_parent_links_are_authority_even_when_children_is_none(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            c = self.conversation(cid="conv-links", leaf="a", responses=[
                self.response("u", "external", "human", "hello", 1700000001000, children=None),
                self.response("a", "u", "assistant", "world", 1700000002000, children=None),
            ])
            artifact = normalize_artifact(materialize_export(self.write_export(root, [c]), root / "out")[0])
            self.assertEqual([m.text for m in artifact.messages if m.search_class == "dialogue"], ["hello", "world"])

    def test_mongo_timestamp_is_deterministic_and_missing_timestamp_stays_unknown(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            c = self.conversation(cid="conv-time", leaf="a", responses=[
                self.response("u", None, "human", "dated", 1700000000123),
                self.response("a", "u", "assistant", "undated", 1700000001000),
            ])
            c["responses"][1]["response"]["create_time"] = None
            artifact = normalize_artifact(materialize_export(self.write_export(root, [c]), root / "out")[0])
            by_text = {m.text: m for m in artifact.messages}
            self.assertAlmostEqual(by_text["dated"].create_time, 1700000000.123)
            self.assertIsNone(by_text["undated"].create_time)

    def test_child_artifacts_are_content_addressed_and_bind_parent_zip_sha(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(root, [self.active_branch_conversation()])
            parent_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            first = materialize_export(source, root / "out")[0]
            first_bytes = first.read_bytes()
            with zipfile.ZipFile(first) as zf:
                manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["source_adapter"], "xai-export")
            self.assertEqual(manifest["source_export_sha256"], parent_sha)
            changed = self.active_branch_conversation()
            changed["responses"][2]["response"]["message"] = "changed active answer"
            self.write_export(root, [changed])
            second = materialize_export(source, root / "out")[0]
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), first_bytes)


    def test_active_leaf_switch_uses_path_specific_session_identity_and_does_not_conflict(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("a","u","assistant","answer A",1700000002000),
                self.response("b","u","assistant","answer B",1700000003000),
            ]
            a=self.conversation(cid="switch",leaf="a",responses=responses,title="Switch")
            source=self.write_export(root,[a])
            first=normalize_artifact(materialize_export(source,root/"v1")[0])
            b=self.conversation(cid="switch",leaf="b",responses=responses,title="Switch")
            self.write_export(root,[b])
            second=normalize_artifact(materialize_export(source,root/"v2")[0])
            self.assertNotEqual(first.session_id,second.session_id)
            self.assertTrue(first.session_id.startswith("switch~branch-"))
            self.assertTrue(second.session_id.startswith("switch~branch-"))

    def test_missing_parent_timestamp_preserves_graph_order(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            c=self.conversation(cid="missing-order",leaf="a",responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("a","u","assistant","answer",1700000002000),
            ])
            c["responses"][0]["response"]["create_time"]=None
            artifact=normalize_artifact(materialize_export(self.write_export(root,[c]),root/"out")[0])
            selected=[m for m in artifact.messages if m.search_class=="dialogue"]
            self.assertEqual([m.text for m in selected],["question","answer"])
            self.assertIsNone(selected[0].create_time)

    def test_backend_member_requires_exact_basename(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            source=root/"xai-export.zip"
            payload={"conversations":[],"media_posts":[],"projects":[],"tasks":[]}
            with zipfile.ZipFile(source,"w") as zf:
                zf.writestr("ttl/backup-prod-grok-backend.json",json.dumps(payload))
            with self.assertRaisesRegex(ValueError,"exactly one"):
                materialize_export(source,root/"out")

    def test_unique_leaf_identity_survives_later_sibling_branch(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            first_c=self.conversation(cid="grow-branch",leaf=None,responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("a","u","assistant","answer A",1700000002000),
            ])
            source=self.write_export(root,[first_c])
            first=normalize_artifact(materialize_export(source,root/"v1")[0])
            second_c=self.conversation(cid="grow-branch",leaf=None,responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("a","u","assistant","answer A",1700000002000),
                self.response("b","u","assistant","answer B",1700000003000),
            ])
            self.write_export(root,[second_c])
            variants=[normalize_artifact(p) for p in materialize_export(source,root/"v2")]
            continued=next(a for a in variants if "answer A" in {m.text for m in a.messages if m.search_class=="dialogue"})
            self.assertEqual(first.session_id,continued.session_id)
            self.assertEqual(first.session_id, "grow-branch")

    def test_undated_ambiguous_siblings_fail_closed_without_stable_provider_order(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("z","u","assistant","old answer",1700000002000),
                self.response("a","u","assistant","new answer",1700000003000),
            ]
            responses[1]["response"]["create_time"]=None
            responses[2]["response"]["create_time"]=None
            c=self.conversation(cid="undated-ambiguous",leaf=None,responses=responses)
            with self.assertRaisesRegex(ValueError,"ambiguous default branch"):
                materialize_export(self.write_export(root,[c]),root/"out")

    def test_children_hint_reordering_does_not_define_base_identity(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            c = self.conversation(cid="conv-hint-reorder", leaf=None, responses=[
                self.response("u", None, "human", "question", 1700000001000, children=[{"response_id":"z"},{"response_id":"a"}]),
                self.response("z", "u", "assistant", "answer Z", 1700000002000),
                self.response("a", "u", "assistant", "answer A", 1700000002000),
            ], title="Hint reorder")
            c["responses"][1]["response"]["create_time"] = None
            c["responses"][2]["response"]["create_time"] = None
            source = self.write_export(root, [c])
            with self.assertRaisesRegex(ValueError, "ambiguous default branch"):
                materialize_export(source, root / "out")

    def test_children_hint_does_not_resolve_undated_default_branch_ambiguity(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            c=self.conversation(cid="conv-hint",leaf=None,responses=[
                self.response("u",None,"human","question",1700000001000,children=[{"response_id":"z"},{"response_id":"a"}]),
                self.response("z","u","assistant","answer Z",1700000002000),
                self.response("a","u","assistant","answer A",1700000003000),
            ],title="Hinted")
            c["responses"][1]["response"]["create_time"]=None
            c["responses"][2]["response"]["create_time"]=None
            with self.assertRaisesRegex(ValueError,"ambiguous default branch"):
                materialize_export(self.write_export(root,[c]),root/"out")

    def test_off_path_subtree_is_topological_even_when_wrappers_are_reversed(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            c=self.conversation(cid="trace-topology",leaf="a",responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("a","u","assistant","selected",1700000002000),
                self.response("c","b","assistant","trace child",1700000004000),
                self.response("b","u","assistant","trace parent",1700000003000),
            ])
            artifact=normalize_artifact(materialize_export(self.write_export(root,[c]),root/"out")[0])
            texts=[m.text for m in artifact.messages]
            self.assertLess(texts.index("trace parent"),texts.index("trace child"))

    def test_linear_continuation_keeps_same_session_identity(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            first_c=self.conversation(cid="linear-grow",leaf="a",responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("a","u","assistant","answer",1700000002000),
            ])
            source=self.write_export(root,[first_c])
            first=normalize_artifact(materialize_export(source,root/"v1")[0])
            second_c=self.conversation(cid="linear-grow",leaf="u2",responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("a","u","assistant","answer",1700000002000),
                self.response("u2","a","human","followup",1700000003000),
            ])
            self.write_export(root,[second_c])
            second=normalize_artifact(materialize_export(source,root/"v2")[0])
            self.assertEqual(first.session_id,second.session_id)
            self.assertEqual(first.session_id,"linear-grow")


    def test_explicit_active_leaf_bypasses_default_branch_inference_when_siblings_ambiguous(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            u=self.response("u",None,"human","q",1700000001000)
            a=self.response("a","u","assistant","A",1700000002000)
            b=self.response("b","u","assistant","B",1700000002000)
            a["response"]["create_time"]=None
            b["response"]["create_time"]=None
            c=self.conversation(cid="conv-explicit-ambiguous",leaf="b",responses=[u,a,b])
            outs=materialize_export(self.write_export(root,[c]),root/"out")
            self.assertEqual(len(outs),1)
            art=normalize_artifact(outs[0])
            dialogue=[m.text for m in art.messages if m.search_class=="dialogue"]
            trace=[m.text for m in art.messages if m.search_class=="trace"]
            self.assertEqual(dialogue,["q","B"])
            self.assertIn("A",trace)

    def test_empty_response_graph_with_active_leaf_is_blocked(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            c=self.conversation(cid="empty-active",leaf="ghost",responses=[])
            with self.assertRaisesRegex(ValueError,"active leaf"):
                materialize_export(self.write_export(root,[c]),root/"out")

    def test_atomic_publisher_never_exposes_final_path_before_replace(self):
        from unittest import mock
        from session_search.xai_export import _publish_content_addressed
        with tempfile.TemporaryDirectory() as td:
            target=pathlib.Path(td)/"child.zip"
            with mock.patch("session_search.xai_export.os.replace",side_effect=RuntimeError("synthetic replace failure")):
                with self.assertRaisesRegex(RuntimeError,"synthetic replace failure"):
                    _publish_content_addressed(target,b"complete-bytes")
            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.glob("*.tmp")),[])

    def test_mismatched_response_conversation_id_is_blocked(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            c = self.active_branch_conversation()
            c["responses"][0]["response"]["conversation_id"] = "wrong"
            with self.assertRaisesRegex(ValueError, "conversation_id"):
                materialize_export(self.write_export(root, [c]), root / "out")


    def test_wrapper_reordering_does_not_change_default_branch_identity(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("b","u","assistant","answer B",1700000003000),
                self.response("a","u","assistant","answer A",1700000002000),
            ]
            first=self.conversation(cid="reorder",leaf=None,responses=responses,title="Reorder")
            source=self.write_export(root,[first])
            v1=[normalize_artifact(p) for p in materialize_export(source,root/"v1")]
            base1=next(a for a in v1 if a.session_id=="reorder")
            second=self.conversation(cid="reorder",leaf=None,responses=[responses[0],responses[2],responses[1]],title="Reorder")
            self.write_export(root,[second])
            v2=[normalize_artifact(p) for p in materialize_export(source,root/"v2")]
            base2=next(a for a in v2 if a.session_id=="reorder")
            d1={m.text for m in base1.messages if m.search_class=="dialogue"}
            d2={m.text for m in base2.messages if m.search_class=="dialogue"}
            self.assertEqual(d1,d2)

    def test_later_lexicographically_earlier_sibling_does_not_steal_base_session(self):
        from session_search.xai_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            first=self.conversation(cid="stable-default",leaf=None,responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("a","u","assistant","answer A",1700000002000),
            ])
            source=self.write_export(root,[first])
            a1=normalize_artifact(materialize_export(source,root/"v1")[0])
            second=self.conversation(cid="stable-default",leaf=None,responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("a","u","assistant","answer A",1700000002000),
                self.response("0","u","assistant","answer later sibling",1700000003000),
            ])
            self.write_export(root,[second])
            variants=[normalize_artifact(p) for p in materialize_export(source,root/"v2")]
            base=next(a for a in variants if a.session_id=="stable-default")
            dialogue={m.text for m in base.messages if m.search_class=="dialogue"}
            self.assertIn("answer A",dialogue)
            self.assertNotIn("answer later sibling",dialogue)
            self.assertEqual(a1.session_id,base.session_id)

    def test_cumulative_branch_extension_keeps_off_path_trace_after_new_dialogue(self):
        from session_search.xai_export import materialize_export
        from session_search.corpus_store import ingest_artifact
        import sqlite3
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); corpus=root/"corpus"
            first=self.conversation(cid="order-grow",leaf="a",responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("a","u","assistant","answer",1700000002000),
                self.response("b","u","assistant","alternate",1700000002500),
            ])
            source=self.write_export(root,[first])
            child1=materialize_export(source,root/"v1")[0]
            session_id=normalize_artifact(child1).session_id
            self.assertTrue(session_id.startswith("order-grow~branch-"))
            self.assertEqual(ingest_artifact(child1,corpus)["status"],"INGESTED")
            second=self.conversation(cid="order-grow",leaf="c",responses=[
                self.response("u",None,"human","question",1700000001000),
                self.response("a","u","assistant","answer",1700000002000),
                self.response("c","a","human","followup",1700000003000),
                self.response("b","u","assistant","alternate",1700000002500),
            ])
            self.write_export(root,[second])
            child2=materialize_export(source,root/"v2")[0]
            self.assertEqual(normalize_artifact(child2).session_id,session_id)
            self.assertEqual(ingest_artifact(child2,corpus)["status"],"INGESTED")
            conn=sqlite3.connect(corpus/"corpus.sqlite3")
            try:
                rows=conn.execute("SELECT text,ordinal FROM messages WHERE session_id=? ORDER BY ordinal",(session_id,)).fetchall()
            finally:
                conn.close()
            texts=[row[0] for row in rows]
            self.assertLess(texts.index("followup"),texts.index("alternate"))



class XaiExportEndToEndTest(unittest.TestCase):
    def test_direct_ingest_uses_existing_corpus_and_search_api(self):
        from session_search.xai_export import ingest_export
        from session_search.search import search_corpus
        helper = XaiExportAdapterTest()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = helper.write_export(root, [helper.active_branch_conversation()])
            corpus = root / "corpus"
            result = ingest_export(source, corpus)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["conversation_count"], 1)
            self.assertEqual(result["artifact_count"], 1)
            active = search_corpus(corpus, "active answer", ["dialogue"], 8)
            abandoned = search_corpus(corpus, "abandoned answer", ["dialogue"], 8)
            traced = search_corpus(corpus, "abandoned answer", ["trace"], 8)
            self.assertEqual(len(active), 1)
            self.assertEqual(abandoned, [])
            self.assertEqual(len(traced), 1)


if __name__ == "__main__":
    unittest.main()
