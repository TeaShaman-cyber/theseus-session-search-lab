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

    def test_reexport_after_title_rename_keeps_session_identity_and_appends_tail(self):
        import sqlite3
        from session_search.corpus_store import CorpusPaths, ingest_artifact, verify_corpus
        from session_search.speed_booster_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first_obj = self.export_object()
            second_obj = self.export_object()
            second_obj["title"] = "Renamed synthetic thread"
            second_obj["exported_at"] = "2026-09-06T11:00:00.000Z"
            second_obj["messages"].append({
                "role": "user",
                "create_time": "2026-09-02T12:00:02.000Z",
                "model": None,
                "content": "new tail after rename",
                "sources": None,
                "images": None,
            })
            first_source = root / "first.json"
            second_source = root / "second.json"
            first_source.write_text(json.dumps(first_obj, ensure_ascii=False), encoding="utf-8")
            second_source.write_text(json.dumps(second_obj, ensure_ascii=False), encoding="utf-8")
            first_artifact = materialize_export(first_source, root / "out1")[0]
            second_artifact = materialize_export(second_source, root / "out2")[0]
            first = normalize_artifact(first_artifact)
            second = normalize_artifact(second_artifact)
            self.assertEqual(first.session_id, second.session_id)
            corpus = root / "corpus"
            self.assertEqual(ingest_artifact(first_artifact, corpus)["status"], "INGESTED")
            self.assertEqual(ingest_artifact(second_artifact, corpus)["status"], "INGESTED")
            with sqlite3.connect(CorpusPaths.from_root(corpus).db) as conn:
                sessions = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
                messages = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
            self.assertEqual(sessions, 1)
            self.assertEqual(messages, 3)
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

    def test_divergent_reexport_with_same_opening_fails_closed(self):
        import sqlite3
        from session_search.corpus_store import CorpusPaths, ingest_artifact, verify_corpus
        from session_search.speed_booster_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first_obj = self.export_object()
            second_obj = self.export_object()
            second_obj["exported_at"] = "2026-09-06T11:00:00.000Z"
            second_obj["messages"][1]["content"] = "divergent second message"
            first_source = root / "first.json"
            second_source = root / "second.json"
            first_source.write_text(json.dumps(first_obj, ensure_ascii=False), encoding="utf-8")
            second_source.write_text(json.dumps(second_obj, ensure_ascii=False), encoding="utf-8")
            first_artifact = materialize_export(first_source, root / "out1")[0]
            second_artifact = materialize_export(second_source, root / "out2")[0]
            self.assertEqual(
                normalize_artifact(first_artifact).session_id,
                normalize_artifact(second_artifact).session_id,
            )
            corpus = root / "corpus"
            self.assertEqual(ingest_artifact(first_artifact, corpus)["status"], "INGESTED")
            with self.assertRaisesRegex(RuntimeError, "FAILED_CONFLICTING_DUPLICATE"):
                ingest_artifact(second_artifact, corpus)
            with sqlite3.connect(CorpusPaths.from_root(corpus).db) as conn:
                artifacts = conn.execute("SELECT count(*) FROM artifacts").fetchone()[0]
                messages = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
            self.assertEqual(artifacts, 1)
            self.assertEqual(messages, 2)
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

    def test_direct_ingest_reuses_legacy_speed_booster_session_identity(self):
        import hashlib
        import sqlite3
        from datetime import datetime
        from session_search.corpus_store import CorpusPaths, ingest_artifact, verify_corpus
        from session_search.speed_booster_export import ingest_export, materialize_export

        def stable_bytes(obj):
            return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

        def rewrite_session_id(source_zip, target_zip, session_id):
            with zipfile.ZipFile(source_zip) as zf:
                manifest = json.loads(zf.read("manifest.json"))
                member = manifest["files"][0]["name"]
                payload = json.loads(zf.read(member))
            payload["conversation_id"] = session_id
            payload_bytes = stable_bytes(payload)
            manifest["session_id"] = session_id
            manifest["files"][0]["bytes"] = len(payload_bytes)
            manifest["files"][0]["sha256"] = hashlib.sha256(payload_bytes).hexdigest()
            with zipfile.ZipFile(target_zip, "w") as zf:
                zf.writestr(member, payload_bytes)
                zf.writestr("manifest.json", stable_bytes(manifest))

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first_obj = self.export_object()
            first_source = root / "first.json"
            first_source.write_text(json.dumps(first_obj, ensure_ascii=False), encoding="utf-8")
            generated = materialize_export(first_source, root / "generated")[0]
            first_time = datetime.fromisoformat(first_obj["messages"][0]["create_time"].replace("Z", "+00:00")).timestamp()
            legacy_digest = hashlib.sha256(stable_bytes([
                first_obj["title"], first_time, first_obj["messages"][0]["role"], first_obj["messages"][0]["content"]
            ])).hexdigest()[:24]
            legacy_session_id = f"speed-booster-v1:{legacy_digest}"
            legacy_artifact = root / "legacy.zip"
            rewrite_session_id(generated, legacy_artifact, legacy_session_id)

            corpus = root / "corpus"
            self.assertEqual(ingest_artifact(legacy_artifact, corpus)["status"], "INGESTED")

            second_obj = self.export_object()
            second_obj["title"] = "Renamed synthetic thread"
            second_obj["exported_at"] = "2026-09-06T11:00:00.000Z"
            second_obj["messages"].append({
                "role": "user",
                "create_time": "2026-09-02T12:00:02.000Z",
                "model": None,
                "content": "new tail after legacy identity",
                "sources": None,
                "images": None,
            })
            second_source = root / "second.json"
            second_source.write_text(json.dumps(second_obj, ensure_ascii=False), encoding="utf-8")
            result = ingest_export(second_source, corpus)
            self.assertEqual(result["status"], "COMPLETE")
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

    def test_same_title_and_first_timestamp_with_different_first_message_content_do_not_merge(self):
        from session_search.speed_booster_export import materialize_export
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first_obj = self.export_object()
            second_obj = self.export_object()
            second_obj["messages"][0]["content"] = "different opening message"
            first_source = root / "first.json"
            second_source = root / "second.json"
            first_source.write_text(json.dumps(first_obj, ensure_ascii=False), encoding="utf-8")
            second_source.write_text(json.dumps(second_obj, ensure_ascii=False), encoding="utf-8")
            first = normalize_artifact(materialize_export(first_source, root / "out1")[0])
            second = normalize_artifact(materialize_export(second_source, root / "out2")[0])
            self.assertNotEqual(first.session_id, second.session_id)

    def test_direct_ingest_returns_hash_from_materialized_source_snapshot(self):
        import hashlib
        from unittest import mock
        from session_search.speed_booster_export import ingest_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "export.json"
            original_obj = self.export_object()
            replacement_obj = self.export_object()
            replacement_obj["messages"][0]["content"] = "replacement snapshot content"
            original = json.dumps(original_obj, ensure_ascii=False).encode("utf-8")
            replacement = json.dumps(replacement_obj, ensure_ascii=False).encode("utf-8")
            source.write_bytes(original)

            original_read_bytes = pathlib.Path.read_bytes
            source_reads = 0
            child_manifest_sha = None

            def racing_read_bytes(path):
                nonlocal source_reads
                if path == source:
                    source_reads += 1
                    return original if source_reads == 1 else replacement
                return original_read_bytes(path)

            def capture_ingest(children, corpus_root):
                nonlocal child_manifest_sha
                with zipfile.ZipFile(children[0]) as zf:
                    child_manifest_sha = json.loads(zf.read("manifest.json"))["source_export_sha256"]
                return {"status": "COMPLETE", "results": []}

            with mock.patch.object(pathlib.Path, "read_bytes", autospec=True, side_effect=racing_read_bytes):
                with mock.patch("session_search.corpus_store.ingest_many", side_effect=capture_ingest):
                    result = ingest_export(source, root / "corpus")

            self.assertEqual(source_reads, 1)
            self.assertEqual(child_manifest_sha, hashlib.sha256(original).hexdigest())
            self.assertEqual(result["source_export_sha256"], child_manifest_sha)

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
        self.assertIn("first message timestamp + first-message role/content", text)


if __name__ == "__main__":
    unittest.main()
