import hashlib
import io
import json
import pathlib
import tempfile
import unittest
import zipfile

from session_search.artifact import normalize_artifact


def write_takeout(path: pathlib.Path, members: dict[str, str]) -> pathlib.Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text.encode("utf-8"))
    return path


class NotebookLmGeminiAdapterTest(unittest.TestCase):
    def test_valid_source_materializes_partial_dialogue_artifact(self):
        from session_search.notebooklm_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(root / "takeout.zip", {
                "Takeout/NotebookLM/Test/Sources/Conversation.html":
                    "<p><strong>User</strong> : hello <em>world</em>"
                    "<strong>Gemini</strong> : answer <b>one</b></p>",
                "Takeout/NotebookLM/Test/Sources/reference.html": "<p>not a conversation</p>",
            })
            outputs = materialize_takeout(source, root / "out")
            self.assertEqual(len(outputs), 1)
            artifact = normalize_artifact(outputs[0])
            self.assertEqual(artifact.source_adapter, "notebooklm-preserved-gemini")
            self.assertEqual(artifact.coverage_state, "PARTIAL_SESSION_SLICE")
            self.assertEqual(
                [(m.role, m.text) for m in artifact.messages],
                [("user", "hello world"), ("assistant", "answer one")],
            )
            self.assertTrue(artifact.session_id.startswith("notebooklm-gemini:"))

    def test_user_only_source_is_skipped_instead_of_promoted_as_conversation(self):
        from session_search.notebooklm_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(root / "takeout.zip", {
                "Takeout/NotebookLM/Test/Sources/user-only.html":
                    "<p><strong>User</strong> : orphan prompt</p>",
            })
            self.assertEqual(materialize_takeout(source, root / "out"), [])

    def test_identical_html_at_distinct_source_paths_keeps_distinct_source_identity(self):
        from session_search.notebooklm_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            dialogue = "<p><strong>User</strong> : same<strong>Gemini</strong> : answer</p>"
            source = write_takeout(root / "takeout.zip", {
                "Takeout/NotebookLM/A/Sources/copy.html": dialogue,
                "Takeout/NotebookLM/B/Sources/copy.html": dialogue,
            })
            artifacts = [normalize_artifact(p) for p in materialize_takeout(source, root / "out")]
            self.assertEqual(len(artifacts), 2)
            self.assertEqual(len({a.session_id for a in artifacts}), 2)
            self.assertEqual(
                [[m.text for m in a.messages] for a in artifacts],
                [["same", "answer"], ["same", "answer"]],
            )

    def test_materialization_is_deterministic_and_binds_parent_takeout_hash(self):
        from session_search.notebooklm_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(root / "takeout.zip", {
                "Takeout/NotebookLM/Test/Sources/chat.html":
                    "<p><strong>User</strong> : q<strong>Gemini</strong> : a</p>",
            })
            parent_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            first = materialize_takeout(source, root / "one")
            second = materialize_takeout(source, root / "two")
            self.assertEqual([p.read_bytes() for p in first], [p.read_bytes() for p in second])
            with zipfile.ZipFile(first[0]) as zf:
                manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["source_export_sha256"], parent_sha)
            self.assertEqual(manifest["source_adapter"], "notebooklm-preserved-gemini")
            self.assertEqual(manifest["preservation_scope"], "PARTIAL_SECONDARY_COPY")

    def test_consecutive_same_role_markers_are_preserved_in_source_order(self):
        from session_search.notebooklm_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(root / "takeout.zip", {
                "Takeout/NotebookLM/Test/Sources/branchy.html":
                    "<p><strong>User</strong> : q1"
                    "<strong>Gemini</strong> : a1"
                    "<strong>Gemini</strong> : a2"
                    "<strong>User</strong> : q2"
                    "<strong>Gemini</strong> : a3</p>",
            })
            artifact = normalize_artifact(materialize_takeout(source, root / "out")[0])
            self.assertEqual(
                [(m.role, m.text) for m in artifact.messages],
                [
                    ("user", "q1"),
                    ("assistant", "a1"),
                    ("assistant", "a2"),
                    ("user", "q2"),
                    ("assistant", "a3"),
                ],
            )

    def test_inspect_receipt_reports_aggregate_source_and_message_counts(self):
        from session_search.notebooklm_gemini import inspect_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(root / "takeout.zip", {
                "Takeout/NotebookLM/Test/Sources/chat.html":
                    "<p><strong>User</strong> : q<strong>Gemini</strong> : a</p>",
                "Takeout/NotebookLM/Test/Sources/user-only.html":
                    "<p><strong>User</strong> : orphan</p>",
                "Takeout/NotebookLM/Test/Sources/reference.html":
                    "<p>plain source</p>",
            })
            first = inspect_takeout(source)
            second = inspect_takeout(source)
            self.assertEqual(first, second)
            self.assertEqual(first["source_html_count"], 3)
            self.assertEqual(first["conversation_count"], 1)
            self.assertEqual(first["skipped_source_count"], 2)
            self.assertEqual(first["message_count"], 2)
            self.assertEqual(first["user_message_count"], 1)
            self.assertEqual(first["assistant_message_count"], 1)
            self.assertEqual(first["preservation_scope"], "PARTIAL_SECONDARY_COPY")

    def test_html_table_cells_and_rows_keep_searchable_boundaries(self):
        from session_search.notebooklm_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(root / "takeout.zip", {
                "Takeout/NotebookLM/Test/Sources/table.html":
                    "<p><strong>User</strong> : show settings"
                    "<strong>Gemini</strong> : "
                    "<table><tr><th>Setting</th><th>Value</th></tr>"
                    "<tr><td>timeout</td><td>30</td></tr></table></p>",
            })
            artifact = normalize_artifact(materialize_takeout(source, root / "out")[0])
            answer = artifact.messages[1].text
            self.assertIn("Setting", answer)
            self.assertIn("Value", answer)
            self.assertIn("timeout", answer)
            self.assertIn("30", answer)
            self.assertNotIn("SettingValue", answer)
            self.assertNotIn("timeout30", answer)

    def test_preformatted_code_preserves_indentation_and_blank_lines(self):
        from session_search.notebooklm_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(root / "takeout.zip", {
                "Takeout/NotebookLM/Test/Sources/code.html":
                    "<p><strong>User</strong> : show code"
                    "<strong>Gemini</strong> : <pre><code>"
                    "if ready:\n    run()\n\nelse:\n    stop()"
                    "</code></pre></p>",
            })
            artifact = normalize_artifact(materialize_takeout(source, root / "out")[0])
            answer = artifact.messages[1].text
            self.assertIn(
                "if ready:\n    run()\n\nelse:\n    stop()",
                answer,
            )

    def test_parent_hash_and_parsed_messages_use_one_captured_archive_snapshot(self):
        from unittest import mock
        import session_search.notebooklm_gemini as adapter

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(root / "takeout.zip", {
                "Takeout/NotebookLM/Test/Sources/chat.html":
                    "<p><strong>User</strong> : old question"
                    "<strong>Gemini</strong> : old answer</p>",
            })
            replacement = write_takeout(root / "replacement.zip", {
                "Takeout/NotebookLM/Test/Sources/chat.html":
                    "<p><strong>User</strong> : replacement question"
                    "<strong>Gemini</strong> : replacement answer</p>",
            }).read_bytes()
            original_parent = source.read_bytes()
            real_read_bytes = pathlib.Path.read_bytes
            mutated = {"done": False}

            def read_then_replace(path):
                data = real_read_bytes(path)
                if path == source and not mutated["done"]:
                    source.write_bytes(replacement)
                    mutated["done"] = True
                return data

            with mock.patch.object(pathlib.Path, "read_bytes", new=read_then_replace):
                child = adapter.materialize_takeout(source, root / "out")[0]

            artifact = normalize_artifact(child)
            self.assertEqual(
                [m.text for m in artifact.messages],
                ["old question", "old answer"],
            )
            with zipfile.ZipFile(child) as zf:
                manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(
                manifest["source_export_sha256"],
                hashlib.sha256(original_parent).hexdigest(),
            )

    def test_ingest_reuses_existing_corpus_search(self):
        from session_search.notebooklm_gemini import ingest_takeout
        from session_search.search import search_corpus

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(root / "takeout.zip", {
                "Takeout/NotebookLM/Test/Sources/chat.html":
                    "<p><strong>User</strong> : strange invariant bridge"
                    "<strong>Gemini</strong> : preserved memory route</p>",
            })
            corpus = root / "corpus"
            result = ingest_takeout(source, corpus)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["conversation_count"], 1)
            self.assertEqual(result["message_count"], 2)
            hits = search_corpus(corpus, "invariant bridge", ["dialogue"], 8)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["representative"]["session_coverage"], "PARTIAL_SESSION_SLICE")


if __name__ == "__main__":
    unittest.main()
