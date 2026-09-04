import copy
import hashlib
import json
import pathlib
import tempfile
import unittest
import zipfile

from session_search.artifact import normalize_artifact


def write_capture(path: pathlib.Path, payloads: list[tuple[str, dict]]) -> pathlib.Path:
    encoded = [
        (name, json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        for name, obj in payloads
    ]
    manifest = {
        "schema": "barn-doctor-export:v1",
        "files": [
            {"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in encoded
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        for name, data in encoded:
            zf.writestr(name, data)
    return path


def detail(session_id: str | None, messages: list[dict], *, title: str = "Synthetic") -> dict:
    obj = {
        "title": title,
        "page_info": {"has_previous_page": False, "has_next_page": False},
        "messages": messages,
    }
    if session_id is not None:
        obj["conversation_id"] = session_id
    return obj


class ArtifactNormalizationTest(unittest.TestCase):
    def test_irrelevant_source_metadata_changes_raw_digest_not_canonical_digest(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            base = {
                "id": "m1",
                "author": {"role": "assistant", "name": None},
                "create_time": 10.0,
                "content": {"content_type": "text", "parts": ["same semantic payload"]},
                "metadata": {"ui_flag": "old"},
            }
            changed = copy.deepcopy(base)
            changed["metadata"]["ui_flag"] = "new"
            first = write_capture(td / "first.zip", [("optional/conversation-1.bin", detail("session-a", [base]))])
            second = write_capture(td / "second.zip", [("optional/conversation-2.bin", detail("session-a", [changed]))])
            a = normalize_artifact(first).messages[0]
            b = normalize_artifact(second).messages[0]
            self.assertNotEqual(a.sources[0].source_object_sha256, b.sources[0].source_object_sha256)
            self.assertEqual(a.canonical_message_sha256, b.canonical_message_sha256)


    def test_legacy_barn_doctor_manifest_does_not_reinterpret_source_adapter(self):
        with tempfile.TemporaryDirectory() as td:
            td=pathlib.Path(td)
            path=write_capture(td/"legacy.zip", [("optional/conversation-1.bin", detail("legacy-session", []))])
            with zipfile.ZipFile(path, "r") as src:
                files={name:src.read(name) for name in src.namelist()}
            manifest=json.loads(files["manifest.json"])
            manifest["source_adapter"]="deepseek-export"
            with zipfile.ZipFile(path,"w") as dst:
                dst.writestr("manifest.json",json.dumps(manifest,sort_keys=True))
                for name,data in files.items():
                    if name!="manifest.json": dst.writestr(name,data)
            self.assertEqual(normalize_artifact(path).source_adapter,"barn-doctor")

    def test_changed_semantic_payload_changes_canonical_digest(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            base = {
                "id": "m1",
                "author": {"role": "assistant"},
                "create_time": 10.0,
                "content": {"content_type": "text", "parts": ["before"]},
            }
            changed = copy.deepcopy(base)
            changed["content"]["parts"] = ["after"]
            first = write_capture(td / "first.zip", [("optional/conversation-1.bin", detail("session-a", [base]))])
            second = write_capture(td / "second.zip", [("optional/conversation-2.bin", detail("session-a", [changed]))])
            a = normalize_artifact(first).messages[0]
            b = normalize_artifact(second).messages[0]
            self.assertNotEqual(a.canonical_message_sha256, b.canonical_message_sha256)

    def test_non_object_metadata_does_not_break_provider_order_extraction(self):
        with tempfile.TemporaryDirectory() as td:
            td=pathlib.Path(td)
            msg={
                "id":"m-meta",
                "author":{"role":"user"},
                "create_time":1.0,
                "content":{"content_type":"text","parts":["legacy metadata"]},
                "metadata":"legacy-string",
            }
            path=write_capture(td/"legacy-meta.zip",[("optional/conversation-1.bin",detail("legacy-meta",[msg]))])
            artifact=normalize_artifact(path)
            self.assertEqual(artifact.messages[0].text,"legacy metadata")
            self.assertIsNone(artifact.messages[0].provider_order)

    def test_unresolved_session_id_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "missing.zip"
            page = {
                "page_info": {"has_previous_page": True, "has_next_page": False},
                "messages": [],
            }
            write_capture(path, [("optional/conversation-messages-1.bin", page)])
            with self.assertRaisesRegex(ValueError, "BLOCKED_UNRESOLVED_SESSION_ID"):
                normalize_artifact(path)

    def test_mixed_session_artifact_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "mixed.zip"
            write_capture(
                path,
                [
                    ("optional/conversation-1.bin", detail("session-a", [])),
                    ("optional/conversation-2.bin", detail("session-b", [])),
                ],
            )
            with self.assertRaisesRegex(ValueError, "BLOCKED_MIXED_SESSION_ARTIFACT"):
                normalize_artifact(path)


if __name__ == "__main__":
    unittest.main()
