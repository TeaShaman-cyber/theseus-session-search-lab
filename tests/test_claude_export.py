import json
import pathlib
import tempfile
import unittest
import zipfile

from session_search.artifact import normalize_artifact


class ClaudeExportAdapterTest(unittest.TestCase):
    def message(self, mid, sender, blocks, created_at, parent=None, **extra):
        value = {
            "uuid": mid,
            "sender": sender,
            "created_at": created_at,
            "updated_at": created_at,
            "content": blocks,
            "attachments": [],
            "files": [],
        }
        if parent is not None:
            value["parent_message_uuid"] = parent
        value.update(extra)
        return value

    def conversation(self, cid="conv-1", messages=None, name="Synthetic Claude"):
        if messages is None:
            messages = [
                self.message("m1", "human", [{"type": "text", "text": "question"}], "2026-09-24T08:00:00Z"),
                self.message("m2", "assistant", [{"type": "text", "text": "answer"}], "2026-09-24T08:00:01Z"),
            ]
        return {
            "uuid": cid,
            "name": name,
            "created_at": "2026-09-24T08:00:00Z",
            "updated_at": "2026-09-24T08:01:00Z",
            "account": {"uuid": "account-redacted"},
            "chat_messages": messages,
        }

    def write_json(self, root, conversations):
        source = root / "conversations.json"
        source.write_text(json.dumps(conversations), encoding="utf-8")
        return source

    def write_zip(self, root, conversations):
        source = root / "conversations-000.zip"
        with zipfile.ZipFile(source, "w") as zf:
            zf.writestr("conversations.json", json.dumps(conversations))
        return source

    def payload(self, artifact_path):
        with zipfile.ZipFile(artifact_path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            member = manifest["files"][0]["name"]
            return manifest, json.loads(zf.read(member))

    def test_linear_json_materializes_dialogue_and_nontext_trace(self):
        from session_search.claude_export import materialize_export

        messages = [
            self.message("m1", "human", [{"type": "text", "text": "question"}], "2026-09-24T08:00:00Z"),
            self.message(
                "m2",
                "assistant",
                [
                    {"type": "thinking", "thinking": "private chain"},
                    {"type": "text", "text": "visible answer"},
                    {"type": "tool_use", "id": "tool-1", "name": "demo", "input": {"x": 1}},
                ],
                "2026-09-24T08:00:01Z",
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            outputs = materialize_export(self.write_json(root, [self.conversation(messages=messages)]), root / "out")
            self.assertEqual(len(outputs), 1)
            artifact = normalize_artifact(outputs[0])
            self.assertEqual(artifact.session_id, "conv-1")
            self.assertEqual([m.text for m in artifact.messages if m.search_class == "dialogue"], ["question", "visible answer"])
            trace = [m for m in artifact.messages if m.search_class == "trace"]
            self.assertEqual(len(trace), 1)
            self.assertEqual(trace[0].content_type, "claude_nontext_trace")
            manifest, payload = self.payload(outputs[0])
            self.assertEqual(manifest["source_adapter"], "claude-export")
            self.assertEqual(payload["claude_snapshot_scope"], "ACCOUNT_EXPORT_SNAPSHOT")
            self.assertTrue(payload["page_info"]["has_previous_page"])

    def test_current_segment_zip_is_accepted(self):
        from session_search.claude_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            outputs = materialize_export(self.write_zip(root, [self.conversation()]), root / "out")
            self.assertEqual(len(outputs), 1)
            manifest, _ = self.payload(outputs[0])
            self.assertEqual(manifest["source_member"], "conversations.json")

    def test_parent_links_materialize_each_leaf_without_flattening(self):
        from session_search.claude_export import materialize_export

        zero = "00000000-0000-0000-0000-000000000000"
        messages = [
            self.message("u", "human", [{"type": "text", "text": "question"}], "2026-09-24T08:00:00Z", parent=zero),
            self.message("a", "assistant", [{"type": "text", "text": "answer A"}], "2026-09-24T08:00:01Z", parent="u"),
            self.message("b", "assistant", [{"type": "text", "text": "answer B"}], "2026-09-24T08:00:02Z", parent="u"),
        ]
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            outputs = materialize_export(self.write_zip(root, [self.conversation(cid="branched", messages=messages)]), root / "out")
            self.assertEqual(len(outputs), 2)
            artifacts = {normalize_artifact(path).session_id: normalize_artifact(path) for path in outputs}
            self.assertIn("branched", artifacts)
            branch_ids = [sid for sid in artifacts if sid.startswith("branched~branch-")]
            self.assertEqual(len(branch_ids), 1)
            self.assertEqual([m.text for m in artifacts["branched"].messages if m.search_class == "dialogue"], ["question", "answer A"])
            self.assertEqual([m.text for m in artifacts[branch_ids[0]].messages if m.search_class == "dialogue"], ["question", "answer B"])

    def test_branched_export_with_tied_child_times_fails_closed(self):
        from session_search.claude_export import materialize_export

        zero = "00000000-0000-0000-0000-000000000000"
        messages = [
            self.message("u", "human", [{"type": "text", "text": "question"}], "2026-09-24T08:00:00Z", parent=zero),
            self.message("a", "assistant", [{"type": "text", "text": "answer A"}], "2026-09-24T08:00:01Z", parent="u"),
            self.message("b", "assistant", [{"type": "text", "text": "answer B"}], "2026-09-24T08:00:01Z", parent="u"),
        ]
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            with self.assertRaisesRegex(ValueError, "ambiguous branch"):
                materialize_export(self.write_json(root, [self.conversation(messages=messages)]), root / "out")

    def test_mixed_missing_parent_graph_fails_closed(self):
        from session_search.claude_export import materialize_export

        zero = "00000000-0000-0000-0000-000000000000"
        messages = [
            self.message("u", "human", [{"type": "text", "text": "question"}], "2026-09-24T08:00:00Z", parent=zero),
            self.message("a", "assistant", [{"type": "text", "text": "answer"}], "2026-09-24T08:00:01Z"),
        ]
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            with self.assertRaisesRegex(ValueError, "incomplete parent graph"):
                materialize_export(self.write_json(root, [self.conversation(messages=messages)]), root / "out")

    def test_duplicate_json_keys_fail_closed(self):
        from session_search.claude_export import materialize_export

        raw = '[{"uuid":"one","uuid":"two","name":"x","chat_messages":[]}]'
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "conversations.json"
            source.write_text(raw, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                materialize_export(source, root / "out")

    def test_materialization_is_byte_stable_and_content_addressed(self):
        from session_search.claude_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_zip(root, [self.conversation()])
            first = materialize_export(source, root / "out-a")
            second = materialize_export(source, root / "out-b")
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 1)
            self.assertEqual(first[0].name, second[0].name)
            self.assertEqual(first[0].read_bytes(), second[0].read_bytes())

    def test_direct_ingest_is_idempotent_and_verifiable(self):
        from session_search.claude_export import ingest_export
        from session_search.corpus_store import verify_corpus

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_zip(root, [self.conversation()])
            corpus = root / "corpus"
            first = ingest_export(source, corpus)
            second = ingest_export(source, corpus)
            self.assertEqual(first["status"], "COMPLETE")
            self.assertEqual(second["status"], "COMPLETE")
            self.assertEqual(first["results"][0]["status"], "INGESTED")
            self.assertEqual(second["results"][0]["status"], "ALREADY_INGESTED")
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
