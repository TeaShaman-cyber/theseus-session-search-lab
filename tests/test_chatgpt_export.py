import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest
import zipfile

from session_search.artifact import normalize_artifact


class ChatGPTExportAdapterTest(unittest.TestCase):
    def message(self, mid, role, content_type, parts, create_time):
        return {
            "id": mid,
            "author": {"role": role, "name": None, "metadata": {}},
            "create_time": create_time,
            "update_time": None,
            "content": {"content_type": content_type, "parts": parts},
            "status": "finished_successfully",
            "end_turn": True,
            "weight": 1.0,
            "metadata": {},
            "recipient": "all",
            "channel": None,
        }

    def node(self, node_id, parent, message):
        return {"id": node_id, "message": message, "parent": parent}

    def conversation(self, cid="conv-1", current="b", mapping=None, title="Synthetic ChatGPT"):
        if mapping is None:
            mapping = {
                "r": self.node("r", None, None),
                "u": self.node("u", "r", self.message("m-u", "user", "text", ["question"], 10.0)),
                "a": self.node("a", "u", self.message("m-a", "assistant", "text", ["answer A"], 20.0)),
                "b": self.node("b", "u", self.message("m-b", "assistant", "text", ["answer B"], 30.0)),
            }
        return {
            "title": title,
            "create_time": 1.0,
            "update_time": 40.0,
            "mapping": mapping,
            "moderation_results": [],
            "current_node": current,
            "conversation_id": cid,
            "id": cid,
        }

    def write_export(self, root: pathlib.Path, conversations, split=False):
        source = root / "chatgpt-export.zip"
        with zipfile.ZipFile(source, "w") as zf:
            if split:
                midpoint = max(1, len(conversations) // 2)
                zf.writestr("conversations-000.json", json.dumps(conversations[:midpoint]))
                zf.writestr("conversations-001.json", json.dumps(conversations[midpoint:]))
            else:
                zf.writestr("conversations-000.json", json.dumps(conversations))
            zf.writestr("chat.html", "<html>ignored</html>")
        return source

    def payload(self, artifact_path):
        with zipfile.ZipFile(artifact_path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            member = manifest["files"][0]["name"]
            return manifest, json.loads(zf.read(member))

    def write_raw_capture(self, path, session_id, messages, title="Synthetic ChatGPT"):
        payload = {
            "conversation_id": session_id,
            "title": title,
            "page_info": {"has_previous_page": True, "has_next_page": False},
            "messages": messages,
        }
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        member = "optional/conversation-raw.bin"
        manifest = {
            "schema": "barn-doctor-export:v1",
            "files": [{"name": member, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
        }
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            zf.writestr(member, data)
        return path

    def test_parent_graph_materializes_every_leaf_and_current_does_not_define_base_identity(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            outputs = materialize_export(self.write_export(root, [self.conversation(current="b")]), root / "out")
            self.assertEqual(len(outputs), 2)
            artifacts = [normalize_artifact(path) for path in outputs]
            by_id = {artifact.session_id: artifact for artifact in artifacts}
            self.assertIn("conv-1", by_id)
            branch_ids = [sid for sid in by_id if sid.startswith("conv-1~branch-")]
            self.assertEqual(len(branch_ids), 1)

            base_dialogue = [m.text for m in by_id["conv-1"].messages if m.search_class == "dialogue"]
            alt_dialogue = [m.text for m in by_id[branch_ids[0]].messages if m.search_class == "dialogue"]
            self.assertEqual(base_dialogue, ["question", "answer A"])
            self.assertEqual(alt_dialogue, ["question", "answer B"])

            manifests = [self.payload(path)[0] for path in outputs]
            current = [m for m in manifests if m["selected_is_current"]]
            self.assertEqual(len(current), 1)
            self.assertTrue(str(current[0]["session_id"]).startswith("conv-1~branch-"))

    def test_switching_current_node_keeps_branch_session_id_set_stable(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first = self.write_export(root, [self.conversation(current="b")])
            first_ids = {normalize_artifact(path).session_id for path in materialize_export(first, root / "out1")}

            second = self.write_export(root, [self.conversation(current="a")])
            second_ids = {normalize_artifact(path).session_id for path in materialize_export(second, root / "out2")}
            self.assertEqual(first_ids, second_ids)

    def test_multimodal_text_stays_dialogue_and_nontext_parts_become_trace(self):
        from session_search.chatgpt_export import materialize_export

        mapping = {
            "r": self.node("r", None, None),
            "u": self.node(
                "u",
                "r",
                self.message(
                    "m-u",
                    "user",
                    "multimodal_text",
                    ["caption", {"content_type": "audio_transcription", "text": "spoken words"}, {"content_type": "image_asset_pointer", "asset_pointer": "file-synthetic"}],
                    10.0,
                ),
            ),
            "a": self.node("a", "u", self.message("m-a", "assistant", "text", ["answer"], 20.0)),
        }
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            artifact = normalize_artifact(
                materialize_export(
                    self.write_export(root, [self.conversation(cid="multi", current="a", mapping=mapping)]),
                    root / "out",
                )[0]
            )
            by_type = {m.content_type: m for m in artifact.messages}
            self.assertEqual([m.text for m in artifact.messages if m.search_class == "dialogue"], ["caption\nspoken words", "answer"])
            self.assertIn("chatgpt_multimodal_trace", by_type)
            self.assertEqual(by_type["chatgpt_multimodal_trace"].search_class, "trace")

    def test_linked_multimodal_projection_reconciles_with_raw_capture_in_both_orders(self):
        from session_search.chatgpt_export import materialize_export
        from session_search.corpus_store import CorpusPaths, ingest_artifact, rebuild_corpus, verify_corpus

        raw_message = self.message(
            "m-u",
            "user",
            "multimodal_text",
            [
                "caption",
                {"content_type": "audio_transcription", "text": "spoken words"},
                {"content_type": "image_asset_pointer", "asset_pointer": "file-synthetic"},
            ],
            10.0,
        )
        mapping = {
            "r": self.node("r", None, None),
            "u": self.node("u", "r", raw_message),
            "a": self.node("a", "u", self.message("m-a", "assistant", "text", ["answer"], 20.0)),
        }
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            projected = materialize_export(
                self.write_export(root, [self.conversation(cid="multi-reconcile", current="a", mapping=mapping)]),
                root / "projected",
            )[0]
            raw = self.write_raw_capture(root / "raw.zip", "multi-reconcile", [raw_message])

            projected_artifact = normalize_artifact(projected)
            raw_artifact = normalize_artifact(raw)
            projected_message = next(m for m in projected_artifact.messages if m.message_id == "m-u")
            raw_normalized = next(m for m in raw_artifact.messages if m.message_id == "m-u")
            self.assertEqual(projected_message.projection_source_canonical_sha256, raw_normalized.canonical_message_sha256)

            for label, order in (("raw-first", (raw, projected)), ("projection-first", (projected, raw))):
                corpus = root / f"corpus-{label}"
                for artifact in order:
                    self.assertEqual(ingest_artifact(artifact, corpus)["status"], "INGESTED")
                self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")
                db = CorpusPaths.from_root(corpus).db
                with sqlite3.connect(db) as conn:
                    row = conn.execute(
                        "SELECT content_type,search_class,text FROM messages WHERE session_id=? AND message_id=?",
                        ("multi-reconcile", "m-u"),
                    ).fetchone()
                    self.assertEqual(row, ("text", "dialogue", "caption\nspoken words"))
                    self.assertEqual(conn.execute(
                        "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?", ("caption",)
                    ).fetchone()[0], 1)
                self.assertEqual(rebuild_corpus(corpus)["status"], "REBUILT")
                self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

    def test_legacy_single_conversations_json_is_supported(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "chatgpt-export.zip"
            with zipfile.ZipFile(source, "w") as zf:
                zf.writestr("conversations.json", json.dumps([self.conversation(cid="legacy")]))
            artifacts = materialize_export(source, root / "out")
            self.assertEqual(len(artifacts), 2)
            self.assertEqual({normalize_artifact(path).coverage_state for path in artifacts}, {"PARTIAL_SESSION_SLICE"})

    def test_thoughts_are_preserved_as_hidden_evidence_not_dialogue(self):
        from session_search.chatgpt_export import materialize_export

        mapping = {
            "r": self.node("r", None, None),
            "u": self.node("u", "r", self.message("m-u", "user", "text", ["question"], 10.0)),
            "t": self.node("t", "u", self.message("m-t", "assistant", "thoughts", ["hidden"], 20.0)),
        }
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            artifact = normalize_artifact(
                materialize_export(
                    self.write_export(root, [self.conversation(cid="thought", current="t", mapping=mapping)]),
                    root / "out",
                )[0]
            )
            hidden = [m for m in artifact.messages if m.content_type == "thoughts"]
            self.assertEqual(len(hidden), 1)
            self.assertEqual(hidden[0].search_class, "hidden")
            self.assertEqual([m.text for m in artifact.messages if m.search_class == "dialogue"], ["question"])

    def test_missing_children_field_is_supported_but_inconsistent_hint_fails_closed(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            ok = self.write_export(root, [self.conversation(cid="no-children")])
            self.assertEqual(len(materialize_export(ok, root / "ok")), 2)

            bad = self.conversation(cid="bad-hint")
            bad["mapping"]["u"]["children"] = ["a"]
            source = self.write_export(root, [bad])
            with self.assertRaisesRegex(ValueError, "children hint inconsistent"):
                materialize_export(source, root / "bad")

    def test_broken_parent_and_nonterminal_current_fail_closed(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            broken = self.conversation(cid="broken")
            broken["mapping"]["a"]["parent"] = "missing"
            with self.assertRaisesRegex(ValueError, "broken parent"):
                materialize_export(self.write_export(root, [broken]), root / "broken-out")

            nonterminal = self.conversation(cid="nonterminal", current="u")
            with self.assertRaisesRegex(ValueError, "current_node must be terminal"):
                materialize_export(self.write_export(root, [nonterminal]), root / "nonterminal-out")

    def test_tied_fork_timestamps_fail_closed_instead_of_using_json_order(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            tied = self.conversation(cid="tied")
            tied["mapping"]["b"]["message"]["create_time"] = 20.0
            with self.assertRaisesRegex(ValueError, "tied child timestamps"):
                materialize_export(self.write_export(root, [tied]), root / "out")

    def test_split_members_duplicate_conversation_identity_is_rejected(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            duplicate = self.conversation(cid="duplicate")
            source = self.write_export(root, [duplicate, duplicate], split=True)
            with self.assertRaisesRegex(ValueError, "duplicate conversation id"):
                materialize_export(source, root / "out")

    def test_child_artifacts_bind_parent_zip_hash_and_are_content_addressed(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(root, [self.conversation(cid="hash")])
            parent_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            first = materialize_export(source, root / "out")
            first_names = [path.name for path in first]
            for path in first:
                manifest, _ = self.payload(path)
                self.assertEqual(manifest["source_export_sha256"], parent_sha)
                self.assertEqual(manifest["snapshot_scope"], "SNAPSHOT_EXPOSED_BRANCHES")
                self.assertEqual(normalize_artifact(path).coverage_state, "PARTIAL_SESSION_SLICE")

            second = materialize_export(source, root / "out")
            self.assertEqual([path.name for path in second], first_names)

            changed = self.conversation(cid="hash")
            changed["mapping"]["a"]["message"]["content"]["parts"] = ["changed A"]
            self.write_export(root, [changed])
            third = materialize_export(source, root / "changed")
            self.assertNotEqual({p.name for p in first}, {p.name for p in third})

    def test_direct_ingest_is_idempotent_and_corpus_verifies(self):
        from session_search.chatgpt_export import ingest_export
        from session_search.corpus_store import verify_corpus

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(root, [self.conversation(cid="ingest")])
            corpus = root / "corpus"
            first = ingest_export(source, corpus)
            self.assertEqual(first["status"], "COMPLETE")
            self.assertEqual(first["conversation_count"], 1)
            self.assertEqual(first["artifact_count"], 2)
            self.assertEqual(first["branch_count"], 2)
            self.assertEqual(first["snapshot_scope"], "SNAPSHOT_EXPOSED_BRANCHES")
            self.assertEqual({item["status"] for item in first["results"]}, {"INGESTED"})
            self.assertEqual({item["coverage_state"] for item in first["results"]}, {"PARTIAL_SESSION_SLICE"})
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

            second = ingest_export(source, corpus)
            self.assertEqual(second["status"], "COMPLETE")
            self.assertEqual({item["status"] for item in second["results"]}, {"ALREADY_INGESTED"})
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
