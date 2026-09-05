import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import zipfile

from session_search.artifact import normalize_artifact


class SpeedBoosterExportAdapterTest(unittest.TestCase):
    def export_object(self):
        return {
            "title": "Synthetic thread",
            "exported_at": "2026-09-05T10:00:00.000Z",
            "created_at": "2026-09-05T09:00:00.000Z",
            "messages": [
                {
                    "role": "user",
                    "create_time": "2026-09-02T12:00:00.000Z",
                    "model": None,
                    "content": "hello from speed booster",
                    "sources": None,
                    "images": None,
                },
                {
                    "role": "assistant",
                    "create_time": "2026-09-02T12:00:01.000Z",
                    "model": "gpt-5-6",
                    "content": "hello back",
                    "sources": [{"title": "example", "url": "https://example.invalid"}],
                    "images": [],
                },
            ],
        }

    def test_minimal_export_materializes_partial_portable_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "export.json"
            source.write_text(json.dumps(self.export_object(), ensure_ascii=False), encoding="utf-8")
            out = root / "out"
            proc = subprocess.run(
                [sys.executable, "-m", "session_search.speed_booster_export", str(source), "--output-dir", str(out)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result["count"], 1)
            artifact_path = pathlib.Path(result["artifacts"][0])
            self.assertTrue(artifact_path.exists())
            artifact = normalize_artifact(artifact_path)
            self.assertEqual(artifact.title, "Synthetic thread")
            self.assertEqual(artifact.coverage_state, "PARTIAL_SESSION_SLICE")
            self.assertEqual([m.role for m in artifact.messages], ["user", "assistant"])
            self.assertEqual([m.text for m in artifact.messages], ["hello from speed booster", "hello back"])

    def test_session_identity_ignores_exported_at(self):
        from session_search.speed_booster_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first_obj = self.export_object()
            second_obj = self.export_object()
            second_obj["exported_at"] = "2026-09-06T11:00:00.000Z"
            first_source = root / "first.json"
            second_source = root / "second.json"
            first_source.write_text(json.dumps(first_obj, ensure_ascii=False), encoding="utf-8")
            second_source.write_text(json.dumps(second_obj, ensure_ascii=False), encoding="utf-8")
            first = normalize_artifact(materialize_export(first_source, root / "out1")[0])
            second = normalize_artifact(materialize_export(second_source, root / "out2")[0])
            self.assertEqual(first.session_id, second.session_id)

    def test_reexport_with_appended_tail_adds_only_novel_message(self):
        import sqlite3
        from session_search.corpus_store import CorpusPaths, ingest_artifact, verify_corpus
        from session_search.speed_booster_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first_obj = self.export_object()
            second_obj = self.export_object()
            second_obj["exported_at"] = "2026-09-06T11:00:00.000Z"
            second_obj["messages"].append({
                "role": "user",
                "create_time": "2026-09-02T12:00:02.000Z",
                "model": None,
                "content": "new tail message",
                "sources": None,
                "images": None,
            })
            first_source = root / "first.json"
            second_source = root / "second.json"
            first_source.write_text(json.dumps(first_obj, ensure_ascii=False), encoding="utf-8")
            second_source.write_text(json.dumps(second_obj, ensure_ascii=False), encoding="utf-8")
            first_artifact = materialize_export(first_source, root / "out1")[0]
            second_artifact = materialize_export(second_source, root / "out2")[0]
            corpus = root / "corpus"
            self.assertEqual(ingest_artifact(first_artifact, corpus)["status"], "INGESTED")
            self.assertEqual(ingest_artifact(second_artifact, corpus)["status"], "INGESTED")
            with sqlite3.connect(CorpusPaths.from_root(corpus).db) as conn:
                sessions = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
                messages = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
            self.assertEqual(sessions, 1)
            self.assertEqual(messages, 3)
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

    def test_export_and_message_metadata_survive_materialization(self):
        from session_search.speed_booster_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "export.json"
            obj = self.export_object()
            source.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            child = materialize_export(source, root / "out")[0]
            with zipfile.ZipFile(child) as zf:
                manifest = json.loads(zf.read("manifest.json"))
                member = manifest["files"][0]["name"]
                payload = json.loads(zf.read(member))
            self.assertEqual(payload["speed_booster_exported_at"], obj["exported_at"])
            self.assertEqual(payload["speed_booster_created_at"], obj["created_at"])
            meta = payload["messages"][1]["metadata"]
            self.assertEqual(meta["speed_booster_create_time_iso"], obj["messages"][1]["create_time"])
            self.assertEqual(meta["speed_booster_model"], "gpt-5-6")
            self.assertEqual(meta["speed_booster_sources"], obj["messages"][1]["sources"])
            self.assertEqual(meta["speed_booster_images"], [])

    def test_one_command_corpus_ingest_populates_existing_search_api(self):
        from session_search.search import search_corpus
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            obj = self.export_object()
            obj["messages"][1]["content"] = "knowledge bridge speed booster anchor"
            source = root / "export.json"
            source.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            corpus = root / "corpus"
            proc = subprocess.run(
                [sys.executable, "-m", "session_search.speed_booster_export", str(source), "--corpus", str(corpus)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            result = json.loads(proc.stdout)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["artifact_count"], 1)
            hits = search_corpus(corpus, "knowledge bridge anchor", ["dialogue"], 8)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["session_coverage"], "PARTIAL_SESSION_SLICE")

    def test_same_title_with_different_first_message_time_does_not_merge(self):
        from session_search.speed_booster_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first_obj = self.export_object()
            second_obj = self.export_object()
            second_obj["messages"][0]["create_time"] = "2026-09-02T12:00:00.500Z"
            first_source = root / "first.json"
            second_source = root / "second.json"
            first_source.write_text(json.dumps(first_obj, ensure_ascii=False), encoding="utf-8")
            second_source.write_text(json.dumps(second_obj, ensure_ascii=False), encoding="utf-8")
            first = normalize_artifact(materialize_export(first_source, root / "out1")[0])
            second = normalize_artifact(materialize_export(second_source, root / "out2")[0])
            self.assertNotEqual(first.session_id, second.session_id)

    def test_unknown_role_stays_trace_and_preserves_raw_role(self):
        from session_search.speed_booster_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            obj = self.export_object()
            obj["messages"][1]["role"] = "toolish-extension-event"
            source = root / "export.json"
            source.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            child = materialize_export(source, root / "out")[0]
            artifact = normalize_artifact(child)
            self.assertEqual(artifact.messages[1].search_class, "trace")
            with zipfile.ZipFile(child) as zf:
                manifest = json.loads(zf.read("manifest.json"))
                payload = json.loads(zf.read(manifest["files"][0]["name"]))
            self.assertEqual(payload["messages"][1]["metadata"]["speed_booster_role"], "toolish-extension-event")

    def test_missing_export_timestamp_is_blocked_as_schema_drift(self):
        from session_search.speed_booster_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            obj = self.export_object()
            del obj["exported_at"]
            source = root / "bad.json"
            source.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT"):
                materialize_export(source, root / "out")

    def test_atomic_publisher_never_exposes_final_path_before_replace(self):
        from unittest import mock
        from session_search.speed_booster_export import _publish_content_addressed
        with tempfile.TemporaryDirectory() as td:
            target = pathlib.Path(td) / "child.zip"
            with mock.patch("session_search.speed_booster_export.os.replace", side_effect=RuntimeError("synthetic replace failure")):
                with self.assertRaisesRegex(RuntimeError, "synthetic replace failure"):
                    _publish_content_addressed(target, b"complete-bytes")
            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_readme_documents_speed_booster_adapter_boundary(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        text = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn("Speed Booster Toolkit", text)
        self.assertIn("session_search.speed_booster_export", text)
        self.assertIn("PARTIAL_SESSION_SLICE", text)
        self.assertIn("title + first message timestamp", text)


if __name__ == "__main__":
    unittest.main()
