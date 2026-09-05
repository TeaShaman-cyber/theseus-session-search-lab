import hashlib
import json
import pathlib
import tempfile
import unittest
import zipfile

from session_search.artifact import normalize_artifact


def _message(message_id: str, text: str, create_time: float) -> dict:
    return {
        "id": message_id,
        "author": {"role": "user"},
        "create_time": create_time,
        "content": {"content_type": "text", "parts": [text]},
    }


def _detail(session_id: str, messages: list[dict], title: str) -> dict:
    return {
        "conversation_id": session_id,
        "title": title,
        "messages": messages,
        "page_info": {"has_previous_page": False, "has_next_page": False},
    }


def _page(messages: list[dict], *, has_previous: bool, has_next: bool) -> dict:
    return {
        "messages": messages,
        "page_info": {
            "has_previous_page": has_previous,
            "has_next_page": has_next,
        },
    }


def _event(request_key: str, endpoint: str, conversation_id: str, seq: int) -> dict:
    return {
        "seq": seq,
        "source": "network",
        "type": "REQUEST_COMPLETED",
        "data": {
            "request_key": request_key,
            "endpointClass": endpoint,
            "conversationId": conversation_id,
            "status": 200,
        },
    }


def _write_capture(path: pathlib.Path, payloads: list[tuple[str, dict]], events: list[dict]) -> pathlib.Path:
    members: list[tuple[str, bytes]] = []
    network = b"".join(
        json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
        for event in events
    )
    members.append(("network-events.jsonl", network))
    for name, obj in payloads:
        members.append(
            (name, json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        )
    manifest = {
        "schema": "barn-doctor-export:v1",
        "files": [
            {
                "name": name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data in members
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        for name, data in members:
            zf.writestr(name, data)
    return path


class BarnRecoveryTest(unittest.TestCase):
    def _api(self):
        try:
            from session_search.barn_recovery import recover_barn_doctor_capture
        except ImportError:
            self.fail("barn recovery API missing")
        return recover_barn_doctor_capture

    def test_recovers_active_session_and_excludes_unrelated_detail_by_request_provenance(self):
        recover = self._api()
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            source = _write_capture(
                td / "mixed.zip",
                [
                    (
                        "optional/conversation-10.bin",
                        _detail("session-a", [_message("m1", "active detail", 1.0)], "Active"),
                    ),
                    (
                        "optional/conversation-11.bin",
                        _detail("session-b", [], "Neighbor"),
                    ),
                    (
                        "optional/conversation-messages-12.bin",
                        _page([_message("m2", "older page", 0.5)], has_previous=False, has_next=True),
                    ),
                ],
                [
                    _event("10", "conversation_get", "session-a", 1),
                    _event("11", "conversation_get", "session-b", 2),
                    _event("12", "conversation_messages", "session-a", 3),
                ],
            )
            with self.assertRaisesRegex(ValueError, "BLOCKED_MIXED_SESSION_ARTIFACT"):
                normalize_artifact(source)

            artifact_path, receipt_path = recover(source, td / "out")
            artifact = normalize_artifact(artifact_path)
            receipt = json.loads(receipt_path.read_text("utf-8"))

            self.assertEqual(artifact.session_id, "session-a")
            self.assertEqual(artifact.title, "Active")
            self.assertEqual([m.text for m in artifact.messages], ["older page", "active detail"])
            self.assertEqual(receipt["selected_session_id"], "session-a")
            self.assertEqual(
                receipt["included_members"],
                ["optional/conversation-10.bin", "optional/conversation-messages-12.bin"],
            )
            self.assertEqual(receipt["excluded_members"], ["optional/conversation-11.bin"])
            self.assertEqual(
                receipt["derived_sha256"],
                hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            )

    def test_recovers_paginated_only_capture_by_injecting_proven_session_identity(self):
        recover = self._api()
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            source = _write_capture(
                td / "pages.zip",
                [
                    (
                        "optional/conversation-messages-21.bin",
                        _page([_message("m1", "first", 1.0)], has_previous=True, has_next=True),
                    ),
                    (
                        "optional/conversation-messages-22.bin",
                        _page([_message("m2", "second", 2.0)], has_previous=False, has_next=True),
                    ),
                ],
                [
                    _event("21", "conversation_messages", "session-a", 1),
                    _event("22", "conversation_messages", "session-a", 2),
                ],
            )
            with self.assertRaisesRegex(ValueError, "BLOCKED_UNRESOLVED_SESSION_ID"):
                normalize_artifact(source)

            artifact_path, _ = recover(source, td / "out")
            artifact = normalize_artifact(artifact_path)

            self.assertEqual(artifact.session_id, "session-a")
            self.assertEqual(len(artifact.messages), 2)
            with zipfile.ZipFile(artifact_path) as zf:
                for name in (
                    "optional/conversation-messages-21.bin",
                    "optional/conversation-messages-22.bin",
                ):
                    obj = json.loads(zf.read(name))
                    self.assertEqual(obj["conversation_id"], "session-a")

    def test_ambiguous_message_page_owners_are_blocked_without_explicit_target(self):
        recover = self._api()
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            source = _write_capture(
                td / "ambiguous.zip",
                [
                    (
                        "optional/conversation-messages-30.bin",
                        _page([_message("m1", "a", 1.0)], has_previous=False, has_next=True),
                    ),
                    (
                        "optional/conversation-messages-31.bin",
                        _page([_message("m2", "b", 2.0)], has_previous=False, has_next=True),
                    ),
                ],
                [
                    _event("30", "conversation_messages", "session-a", 1),
                    _event("31", "conversation_messages", "session-b", 2),
                ],
            )
            with self.assertRaisesRegex(ValueError, "BLOCKED_AMBIGUOUS_RECOVERY_SESSION"):
                recover(source, td / "out")

    def test_conflicting_request_provenance_is_blocked(self):
        recover = self._api()
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            source = _write_capture(
                td / "conflict.zip",
                [
                    (
                        "optional/conversation-messages-40.bin",
                        _page([_message("m1", "conflict", 1.0)], has_previous=False, has_next=True),
                    ),
                ],
                [
                    _event("40", "conversation_messages", "session-a", 1),
                    _event("40", "conversation_messages", "session-b", 2),
                ],
            )
            with self.assertRaisesRegex(ValueError, "BLOCKED_CONTRADICTORY_MEMBER_PROVENANCE"):
                recover(source, td / "out")


if __name__ == "__main__":
    unittest.main()
