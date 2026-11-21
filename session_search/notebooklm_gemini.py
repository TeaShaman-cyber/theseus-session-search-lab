from __future__ import annotations

import argparse
import dataclasses
import hashlib
import html.parser
import json
import os
import pathlib
import re
import shutil
import tempfile
import uuid
import zipfile

from .artifact import safe_members


SCHEMA = "theseus.session-search.notebooklm-gemini-child.v1"
ADAPTER = "notebooklm-preserved-gemini"
PRESERVATION_SCOPE = "PARTIAL_SECONDARY_COPY"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_MARKER_RE = re.compile(r"<strong>\s*(User|Gemini)\s*</strong>\s*:", re.IGNORECASE)
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _stable_json_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _PlainTextParser(html.parser.HTMLParser):
    _BLOCK_START = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
    _BLOCK_END = _BLOCK_START | {"li", "ol", "ul"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self._BLOCK_START:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCK_END:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts).replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
        lines: list[str] = []
        for line in raw.split("\n"):
            normalized = re.sub(r"[ \t\f\v]+", " ", line).strip()
            if normalized:
                lines.append(normalized)
        return "\n".join(lines).strip()


@dataclasses.dataclass(frozen=True)
class _ConversationSource:
    member_name: str
    member_sha256: str
    session_id: str
    title: str
    messages: tuple[dict, ...]


@dataclasses.dataclass(frozen=True)
class _MaterializedSnapshot:
    artifacts: tuple[pathlib.Path, ...]
    source_export_sha256: str
    source_export_bytes: int
    source_html_count: int
    conversation_count: int
    skipped_source_count: int
    message_count: int
    user_message_count: int
    assistant_message_count: int


def _html_to_text(fragment: str) -> str:
    parser = _PlainTextParser()
    parser.feed(fragment)
    parser.close()
    return parser.text()


def _source_session_id(member_name: str, member_sha256: str) -> str:
    digest = _sha256(_stable_json_bytes([member_name, member_sha256]))
    return f"notebooklm-gemini:{digest[:32]}"


def _message_id(session_id: str, ordinal: int, role: str, text: str) -> str:
    digest = _sha256(_stable_json_bytes([session_id, int(ordinal), role, text]))
    return f"notebooklm-gemini-message:{digest}"


def _parse_conversation(member_name: str, raw: bytes) -> _ConversationSource | None:
    try:
        source_html = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"BLOCKED_UNSUPPORTED_NOTEBOOKLM_GEMINI: invalid UTF-8: {member_name}") from exc

    markers = list(_MARKER_RE.finditer(source_html))
    if not markers:
        return None
    labels = [match.group(1).lower() for match in markers]
    if "user" not in labels or "gemini" not in labels:
        return None

    member_sha = _sha256(raw)
    session_id = _source_session_id(member_name, member_sha)
    messages: list[dict] = []
    for ordinal, marker in enumerate(markers):
        start = marker.end()
        end = markers[ordinal + 1].start() if ordinal + 1 < len(markers) else len(source_html)
        text = _html_to_text(source_html[start:end])
        label = marker.group(1)
        role = "user" if label.lower() == "user" else "assistant"
        messages.append(
            {
                "id": _message_id(session_id, ordinal, role, text),
                "author": {"role": role},
                "create_time": None,
                "content": {"content_type": "text", "parts": [text]},
                "metadata": {
                    "notebooklm_source_member": member_name,
                    "notebooklm_source_member_sha256": member_sha,
                    "notebooklm_role_label": label,
                    "notebooklm_preservation_scope": PRESERVATION_SCOPE,
                    "session_search_order": ordinal,
                },
            }
        )

    title = pathlib.PurePosixPath(member_name).stem
    return _ConversationSource(
        member_name=member_name,
        member_sha256=member_sha,
        session_id=session_id,
        title=title,
        messages=tuple(messages),
    )


def _candidate_source_members(names: list[str]) -> list[str]:
    result = []
    for name in names:
        path = pathlib.PurePosixPath(name)
        if path.suffix.lower() != ".html":
            continue
        if len(path.parts) < 5:
            continue
        if path.parts[0] != "Takeout" or path.parts[1] != "NotebookLM":
            continue
        if "Sources" not in path.parts[2:-1]:
            continue
        result.append(name)
    return sorted(result)


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, _FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def _portable_zip_bytes(member: str, payload_bytes: bytes, manifest: dict) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        _zip_write(zf, member, payload_bytes)
        _zip_write(zf, "manifest.json", _stable_json_bytes(manifest))
    return buffer.getvalue()


def _publish_content_addressed(target: pathlib.Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError("BLOCKED_NOTEBOOKLM_GEMINI: content-address collision")
        return
    temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("BLOCKED_NOTEBOOKLM_GEMINI: concurrent content-address collision")
            return
        os.replace(temp, target)
        if target.read_bytes() != data:
            raise ValueError("BLOCKED_NOTEBOOKLM_GEMINI: published artifact mismatch")
    finally:
        if temp.exists():
            temp.unlink()


def _materialize_takeout_snapshot(source: pathlib.Path, output_dir: pathlib.Path) -> _MaterializedSnapshot:
    source = pathlib.Path(source)
    output_dir = pathlib.Path(output_dir)
    raw_export = source.read_bytes()
    parent_sha = _sha256(raw_export)

    conversations: list[_ConversationSource] = []
    with zipfile.ZipFile(source) as zf:
        names = safe_members(zf)
        candidates = _candidate_source_members(names)
        for member_name in candidates:
            parsed = _parse_conversation(member_name, zf.read(member_name))
            if parsed is not None:
                conversations.append(parsed)

    pending: list[tuple[pathlib.Path, pathlib.Path]] = []
    outputs: list[pathlib.Path] = []
    staging_root = pathlib.Path(tempfile.mkdtemp(prefix="session-search-notebooklm-gemini-stage-"))
    try:
        for conversation in conversations:
            payload = {
                "conversation_id": conversation.session_id,
                "title": conversation.title,
                "messages": list(conversation.messages),
                "page_info": {
                    "has_previous_page": True,
                    "has_next_page": False,
                },
                "notebooklm_source_member": conversation.member_name,
                "notebooklm_source_member_sha256": conversation.member_sha256,
                "notebooklm_parent_takeout_sha256": parent_sha,
                "notebooklm_preservation_scope": PRESERVATION_SCOPE,
            }
            payload_bytes = _stable_json_bytes(payload)
            path_hash = _sha256(conversation.member_name.encode("utf-8"))[:12]
            safe_title = _SAFE_ID.sub("_", conversation.title).strip("._")[:80] or "conversation"
            member = f"optional/conversation-notebooklm-gemini-{path_hash}.bin"
            manifest = {
                "schema": SCHEMA,
                "source_adapter": ADAPTER,
                "source_export_sha256": parent_sha,
                "source_export_bytes": len(raw_export),
                "source_member": conversation.member_name,
                "source_member_sha256": conversation.member_sha256,
                "session_id": conversation.session_id,
                "preservation_scope": PRESERVATION_SCOPE,
                "files": [
                    {
                        "name": member,
                        "bytes": len(payload_bytes),
                        "sha256": _sha256(payload_bytes),
                    }
                ],
            }
            archive_bytes = _portable_zip_bytes(member, payload_bytes, manifest)
            archive_sha = _sha256(archive_bytes)
            target = output_dir / f"gemini-notebooklm-{safe_title}-{path_hash}-{archive_sha[:16]}.zip"
            stage_path = staging_root / f"{len(pending):08d}.zip"
            stage_path.write_bytes(archive_bytes)
            pending.append((target, stage_path))
            outputs.append(target)

        output_dir.mkdir(parents=True, exist_ok=True)
        for target, stage_path in pending:
            _publish_content_addressed(target, stage_path.read_bytes())
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    message_count = sum(len(conversation.messages) for conversation in conversations)
    user_count = sum(
        1
        for conversation in conversations
        for message in conversation.messages
        if (message.get("author") or {}).get("role") == "user"
    )
    assistant_count = sum(
        1
        for conversation in conversations
        for message in conversation.messages
        if (message.get("author") or {}).get("role") == "assistant"
    )
    return _MaterializedSnapshot(
        artifacts=tuple(outputs),
        source_export_sha256=parent_sha,
        source_export_bytes=len(raw_export),
        source_html_count=len(candidates),
        conversation_count=len(conversations),
        skipped_source_count=len(candidates) - len(conversations),
        message_count=message_count,
        user_message_count=user_count,
        assistant_message_count=assistant_count,
    )


def materialize_takeout(source: pathlib.Path, output_dir: pathlib.Path) -> list[pathlib.Path]:
    return list(_materialize_takeout_snapshot(source, output_dir).artifacts)


def inspect_takeout(source: pathlib.Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="session-search-notebooklm-gemini-inspect-") as td:
        snapshot = _materialize_takeout_snapshot(source, pathlib.Path(td))
    return {
        "schema": SCHEMA,
        "source_adapter": ADAPTER,
        "preservation_scope": PRESERVATION_SCOPE,
        "source_export_sha256": snapshot.source_export_sha256,
        "source_export_bytes": snapshot.source_export_bytes,
        "source_html_count": snapshot.source_html_count,
        "conversation_count": snapshot.conversation_count,
        "skipped_source_count": snapshot.skipped_source_count,
        "message_count": snapshot.message_count,
        "user_message_count": snapshot.user_message_count,
        "assistant_message_count": snapshot.assistant_message_count,
    }


def ingest_takeout(source: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    from .corpus_store import ingest_many

    source = pathlib.Path(source)
    with tempfile.TemporaryDirectory(prefix="session-search-notebooklm-gemini-") as td:
        snapshot = _materialize_takeout_snapshot(source, pathlib.Path(td))
        child_ids = {
            str(path): f"notebooklm-gemini-child-sha256:{_sha256(path.read_bytes())}"
            for path in snapshot.artifacts
        }
        result = ingest_many(list(snapshot.artifacts), pathlib.Path(corpus_root))
        for item in result.get("results", []):
            source_value = item.get("source")
            if source_value in child_ids:
                item["source"] = child_ids[source_value]
    return {
        **result,
        "conversation_count": snapshot.conversation_count,
        "artifact_count": len(snapshot.artifacts),
        "message_count": snapshot.message_count,
        "user_message_count": snapshot.user_message_count,
        "assistant_message_count": snapshot.assistant_message_count,
        "source_html_count": snapshot.source_html_count,
        "skipped_source_count": snapshot.skipped_source_count,
        "source_export_sha256": snapshot.source_export_sha256,
        "preservation_scope": PRESERVATION_SCOPE,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rescue NotebookLM-preserved Gemini conversations from a Google Takeout ZIP."
    )
    parser.add_argument("source", type=pathlib.Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output-dir", type=pathlib.Path)
    destination.add_argument("--corpus", type=pathlib.Path)
    destination.add_argument("--inspect", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.inspect:
            print(json.dumps(inspect_takeout(args.source), ensure_ascii=False, sort_keys=True))
            return 0
        if args.corpus is not None:
            result = ingest_takeout(args.source, args.corpus)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result.get("status") == "COMPLETE" else 1
        outputs = materialize_takeout(args.source, args.output_dir)
    except Exception as exc:
        print(f"NOTEBOOKLM GEMINI RESCUE FAILED: {exc}")
        return 1

    print(json.dumps({"artifacts": [str(path) for path in outputs], "count": len(outputs)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
