import hashlib
import json
import pathlib
import tempfile
import unittest
import zipfile

from session_search.artifact import normalize_artifact


class DeepSeekExportAdapterTest(unittest.TestCase):
    def synthetic_export(self, root: pathlib.Path) -> pathlib.Path:
        source = root / "conversations.json"
        source.write_text(json.dumps([
            {
                "id": "conv-alpha",
                "title": "Robot notes",
                "inserted_at": "2026-06-01T10:00:00+08:00",
                "updated_at": "2026-06-01T10:01:00+08:00",
                "mapping": {
                    "root": {"id": "root", "parent": None, "children": ["u1"], "message": None},
                    "u1": {"id": "u1", "parent": "root", "children": ["a1"], "message": {"inserted_at": "2026-06-01T10:00:00+08:00", "model": "deepseek-chat", "fragments": [{"type": "REQUEST", "content": "Unitree cyber cerebellum"}]}},
                    "a1": {"id": "a1", "parent": "u1", "children": [], "message": {"inserted_at": "2026-06-01T10:00:01+08:00", "model": "deepseek-chat", "fragments": [{"type": "RESPONSE", "content": "Use MCP as the feedback bridge."}]}},
                },
            },
            {
                "id": "conv-beta",
                "title": "Other chat",
                "inserted_at": "2026-06-02T10:00:00+08:00",
                "updated_at": "2026-06-02T10:01:00+08:00",
                "mapping": {
                    "root": {"id": "root", "parent": None, "children": ["u2"], "message": None},
                    "u2": {"id": "u2", "parent": "root", "children": [], "message": {"inserted_at": "2026-06-02T10:00:00+08:00", "model": "deepseek-chat", "fragments": [{"type": "REQUEST", "content": "unrelated gardening"}]}},
                },
            },
        ], ensure_ascii=False), encoding="utf-8")
        return source


    def _conversation(self, session_id: str, content: str) -> dict:
        return {
            "id": session_id,
            "title": session_id,
            "mapping": {
                "root": {"id": "root", "parent": None, "children": ["1"], "message": None},
                "1": {"id": "1", "parent": "root", "children": [], "message": {
                    "inserted_at": "2026-09-03T12:00:00+00:00",
                    "model": "deepseek-chat",
                    "fragments": [{"type": "REQUEST", "content": content}],
                }},
            },
        }

    def test_materialization_is_deterministic_and_preserves_parent_export_sha(self):
        from session_search.deepseek_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.synthetic_export(root)
            parent_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            first = materialize_export(source, root / "one")
            second = materialize_export(source, root / "two")
            self.assertEqual(len(first), 2)
            self.assertTrue(first[0].name.startswith("deepseek-conv-alpha-8d4f6cf15890-"))
            self.assertTrue(first[0].name.endswith(".zip"))
            self.assertTrue(first[1].name.startswith("deepseek-conv-beta-9faa2efd77b6-"))
            self.assertTrue(first[1].name.endswith(".zip"))
            self.assertEqual([p.read_bytes() for p in first], [p.read_bytes() for p in second])
            with zipfile.ZipFile(first[0]) as zf:
                manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["source_adapter"], "deepseek-export")
            self.assertEqual(manifest["source_export_sha256"], parent_sha)

    def test_child_artifact_reuses_normalizer_with_dialogue_roles(self):
        from session_search.deepseek_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            child = materialize_export(self.synthetic_export(root), root / "children")[0]
            artifact = normalize_artifact(child)
            self.assertEqual(artifact.session_id, "conv-alpha")
            self.assertEqual(artifact.title, "Robot notes")
            self.assertEqual(artifact.source_adapter, "deepseek-export")
            self.assertEqual(artifact.coverage_state, "COMPLETE_EXPOSED_CONVERSATION")
            self.assertEqual([(m.role, m.text) for m in artifact.messages], [
                ("user", "Unitree cyber cerebellum"),
                ("assistant", "Use MCP as the feedback bridge."),
            ])

    def test_invalid_mapping_is_blocked_instead_of_guessed(self):
        from session_search.deepseek_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "bad.json"
            source.write_text(json.dumps([{"id":"broken","title":"x","mapping":{"x":{"id":"x","parent":"missing","children":[],"message":None}}}]))
            with self.assertRaisesRegex(ValueError, "BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT"):
                materialize_export(source, root / "out")

    def test_sanitized_child_filenames_remain_collision_free(self):
        from session_search.deepseek_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "conversations.json"
            source.write_text(json.dumps([
                self._conversation("a/b", "one"),
                self._conversation("a?b", "two"),
            ]), encoding="utf-8")
            outputs = materialize_export(source, root / "out")
            self.assertEqual(2, len(outputs))
            self.assertEqual(2, len({path.name for path in outputs}))
            self.assertTrue(all(path.exists() for path in outputs))


    def test_reexport_to_same_directory_preserves_prior_artifact(self):
        from session_search.deepseek_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "conversations.json"
            source.write_text(json.dumps([self._conversation("same", "first")]), encoding="utf-8")
            first = materialize_export(source, root / "out")[0]
            first_bytes = first.read_bytes()
            source.write_text(json.dumps([self._conversation("same", "second")]), encoding="utf-8")
            second = materialize_export(source, root / "out")[0]
            self.assertNotEqual(first, second)
            self.assertTrue(first.exists())
            self.assertEqual(first.read_bytes(), first_bytes)
            self.assertTrue(second.exists())

    def test_branched_conversation_materializes_one_session_variant_per_leaf(self):
        from session_search.deepseek_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            c = self._conversation("branch", "shared question")
            c["mapping"]["1"]["children"] = ["a", "b"]
            c["mapping"]["a"] = {"id":"a","parent":"1","children":[],"message":{
                "inserted_at":"2026-09-03T12:00:01+00:00","model":"deepseek-chat",
                "fragments":[{"type":"RESPONSE","content":"answer A"}]}}
            c["mapping"]["b"] = {"id":"b","parent":"1","children":[],"message":{
                "inserted_at":"2026-09-03T12:00:02+00:00","model":"deepseek-chat",
                "fragments":[{"type":"RESPONSE","content":"answer B"}]}}
            source = root / "conversations.json"
            source.write_text(json.dumps([c]), encoding="utf-8")
            outputs = materialize_export(source, root / "out")
            self.assertEqual(len(outputs), 2)
            artifacts = [normalize_artifact(x) for x in outputs]
            self.assertEqual(len({a.session_id for a in artifacts}), 2)
            texts = [{m.text for m in a.messages} for a in artifacts]
            self.assertTrue(all("shared question" in x for x in texts))
            self.assertEqual(sum("answer A" in x for x in texts), 1)
            self.assertEqual(sum("answer B" in x for x in texts), 1)
            self.assertTrue(all(not ({"answer A", "answer B"} <= x) for x in texts))

    def test_missing_timestamp_is_preserved_as_unknown_not_epoch(self):
        from session_search.deepseek_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            c = self._conversation("undated", "hello")
            del c["mapping"]["1"]["message"]["inserted_at"]
            source = root / "conversations.json"
            source.write_text(json.dumps([c]), encoding="utf-8")
            artifact = normalize_artifact(materialize_export(source, root / "out")[0])
            self.assertEqual(len(artifact.messages), 1)
            self.assertIsNone(artifact.messages[0].create_time)
            self.assertIsNone(artifact.pages[0].min_create_time)
            self.assertIsNone(artifact.pages[0].max_create_time)

    def test_explicit_empty_fragment_collection_is_preserved_as_trace_placeholder(self):
        from session_search.deepseek_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            c = self._conversation("empty-fragments", "hello")
            c["mapping"]["1"]["message"]["fragments"] = []
            source = root / "conversations.json"
            source.write_text(json.dumps([c]), encoding="utf-8")
            artifact = normalize_artifact(materialize_export(source, root / "out")[0])
            self.assertEqual(len(artifact.messages), 1)
            self.assertEqual(artifact.messages[0].search_class, "trace")
            self.assertEqual(artifact.messages[0].text, "")

    def test_missing_fragment_collection_is_blocked(self):
        from session_search.deepseek_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            c = self._conversation("missing-fragments", "hello")
            del c["mapping"]["1"]["message"]["fragments"]
            source = root / "conversations.json"
            source.write_text(json.dumps([c]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fragments"):
                materialize_export(source, root / "out")

    def test_fragment_message_ids_use_unambiguous_tuple_encoding(self):
        from session_search.deepseek_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            c = self._conversation("ids", "first")
            c["mapping"]["1"]["id"] = "a"
            c["mapping"]["root"]["children"] = ["a"]
            c["mapping"]["a"] = c["mapping"].pop("1")
            c["mapping"]["a"]["fragments"] = c["mapping"]["a"].get("fragments")
            c["mapping"]["a"]["message"]["fragments"].append({"type":"REQUEST","content":"second fragment"})
            c["mapping"]["a"]["children"] = ["a:1"]
            c["mapping"]["a:1"] = {"id":"a:1","parent":"a","children":[],"message":{
                "inserted_at":"2026-09-03T12:00:01+00:00","model":"deepseek-chat",
                "fragments":[{"type":"RESPONSE","content":"third"}]}}
            source = root / "conversations.json"
            source.write_text(json.dumps([c]), encoding="utf-8")
            artifact = normalize_artifact(materialize_export(source, root / "out")[0])
            ids = [m.message_id for m in artifact.messages]
            self.assertEqual(len(ids), 3)
            self.assertEqual(len(set(ids)), 3)

    def test_ingest_reuses_materialized_source_snapshot_hash(self):
        from unittest import mock
        from session_search.deepseek_export import ingest_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "conversations.json"
            source.write_text(json.dumps([self._conversation("snapshot", "hello")]), encoding="utf-8")
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            def mutate_then_succeed(children, corpus_root):
                source.unlink()
                return {"status":"COMPLETE"}
            with mock.patch("session_search.corpus_store.ingest_many", side_effect=mutate_then_succeed):
                result = ingest_export(source, root / "corpus")
            self.assertEqual(result["source_export_sha256"], expected)
            self.assertEqual(result["conversation_count"], 1)
            self.assertEqual(result["artifact_count"], 1)

    def test_timezone_naive_timestamp_is_rejected(self):
        from session_search.deepseek_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "conversations.json"
            conversation = self._conversation("tz-test", "hello")
            conversation["mapping"]["1"]["message"]["inserted_at"] = "2026-09-03T12:00:00"
            source.write_text(json.dumps([conversation]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "timezone-aware"):
                materialize_export(source, root / "out")


if __name__ == "__main__":
    unittest.main()

class DeepSeekEndToEndCorpusTest(unittest.TestCase):
    def test_one_command_ingest_populates_existing_corpus_search(self):
        from session_search.deepseek_export import ingest_export
        from session_search.search import search_corpus

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = DeepSeekExportAdapterTest().synthetic_export(root)
            corpus = root / "corpus"
            result = ingest_export(source, corpus)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["conversation_count"], 2)
            hits = search_corpus(corpus, "Unitree cerebellum", ["dialogue"], 8)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["session_id"], "conv-alpha")
            self.assertEqual(hits[0]["session_title"], "Robot notes")
            self.assertEqual(hits[0]["session_coverage"], "COMPLETE_EXPOSED_CONVERSATION")


class DeepSeekLongChainRegressionTest(unittest.TestCase):
    def test_long_official_mapping_chain_does_not_depend_on_python_recursion_limit(self):
        from session_search.deepseek_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            mapping = {"root": {"id": "root", "parent": None, "children": ["n0"], "message": None}}
            count = 1200
            for i in range(count):
                node_id = f"n{i}"
                child = f"n{i+1}" if i + 1 < count else None
                mapping[node_id] = {
                    "id": node_id,
                    "parent": "root" if i == 0 else f"n{i-1}",
                    "children": [child] if child else [],
                    "message": {
                        "inserted_at": f"2026-06-01T10:{i // 60:02d}:{i % 60:02d}+08:00",
                        "model": "deepseek-chat",
                        "fragments": [{"type": "REQUEST" if i % 2 == 0 else "RESPONSE", "content": f"message {i}"}],
                    },
                }
            source = root / "long.json"
            source.write_text(json.dumps([{"id":"long","title":"Long","mapping":mapping}]), encoding="utf-8")
            children = materialize_export(source, root / "out")
            artifact = normalize_artifact(children[0])
            self.assertEqual(len(artifact.messages), count)
