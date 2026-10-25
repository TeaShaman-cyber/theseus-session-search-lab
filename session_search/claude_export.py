from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import tempfile
import uuid
import zipfile

SCHEMA = "theseus.session-search.claude-export-child.v1"
ADAPTER = "claude-export"
SNAPSHOT_SCOPE = "ACCOUNT_EXPORT_SNAPSHOT"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_SAFE_ID = re.compile(r"[^A-Za-z0-9._~-]+")
_CONVERSATIONS_MEMBER = re.compile(r"(?:^|/)conversations(?:-\d+)?\.json$")
_ROOT_PARENT_SENTINELS = {
    "00000000-0000-4000-8000-000000000000",
    "00000000-0000-0000-0000-000000000000",
}


@dataclasses.dataclass(frozen=True)
class _MaterializedSnapshot:
    artifacts: tuple[pathlib.Path, ...]
    source_export_sha256: str
    conversation_count: int
    branch_count: int
    parser_mode: str


def _stable_json_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object_without_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: duplicate JSON key {key!r}")
        result[key] = value
    return result


def _parse_json(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys)
    except ValueError as exc:
        if str(exc).startswith("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT:"):
            raise
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: invalid JSON") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: invalid JSON") from exc


def _parse_time(value: object) -> float | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: invalid timestamp type")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: timestamp must be timezone-aware")
    return parsed.timestamp()


def _load_source(source: pathlib.Path) -> tuple[bytes, list[tuple[str, dict]], str]:
    source = pathlib.Path(source)
    raw = source.read_bytes()
    rows: list[tuple[str, dict]] = []
    if zipfile.is_zipfile(io.BytesIO(raw)):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                names = zf.namelist()
                if len(names) != len(set(names)):
                    raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: duplicate ZIP member name")
                members = sorted(name for name in names if _CONVERSATIONS_MEMBER.search(name))
                if not members:
                    raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: conversations JSON missing from ZIP")
                for member in members:
                    parsed = _parse_json(zf.read(member))
                    if not isinstance(parsed, list):
                        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: conversations top level must be list")
                    for conversation in parsed:
                        if not isinstance(conversation, dict):
                            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: conversation must be object")
                        rows.append((member, conversation))
        except zipfile.BadZipFile as exc:
            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: invalid ZIP") from exc
        return raw, rows, "zip"

    parsed = _parse_json(raw)
    if not isinstance(parsed, list):
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: conversations top level must be list")
    for conversation in parsed:
        if not isinstance(conversation, dict):
            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: conversation must be object")
        rows.append(("<direct-json>", conversation))
    return raw, rows, "json"


def _source_conversation_id(conversation: dict) -> str:
    value = conversation.get("uuid")
    if not isinstance(value, str) or not value:
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: conversation uuid missing")
    return value


def _source_title(conversation: dict) -> str:
    value = conversation.get("name")
    if not isinstance(value, str):
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: conversation name missing")
    return value


def _source_message_id(message: dict) -> str:
    value = message.get("uuid")
    if not isinstance(value, str) or not value:
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: message uuid missing")
    return value


def _message_time(message: dict) -> float | None:
    return _parse_time(message.get("created_at"))


def _messages(conversation: dict) -> list[dict]:
    value = conversation.get("chat_messages")
    if not isinstance(value, list):
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: chat_messages must be list")
    result: list[dict] = []
    seen: set[str] = set()
    for message in value:
        if not isinstance(message, dict):
            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: message must be object")
        message_id = _source_message_id(message)
        if message_id in seen:
            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: duplicate message uuid")
        seen.add(message_id)
        _message_time(message)
        updated = message.get("updated_at")
        if updated not in (None, ""):
            _parse_time(updated)
        result.append(message)
    return result


def _graph_paths(messages: list[dict]) -> tuple[list[list[dict]], bool]:
    if not messages:
        return [[]], False
    parent_keys = ["parent_message_uuid" in message for message in messages]
    if any(parent_keys) and not all(parent_keys):
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: incomplete parent graph")
    if not any(parent_keys):
        return [list(messages)], False

    by_id = {_source_message_id(message): message for message in messages}
    children: dict[str, list[str]] = {message_id: [] for message_id in by_id}
    roots: list[str] = []
    for message_id, message in by_id.items():
        parent = message.get("parent_message_uuid")
        if parent in (None, "") or parent in _ROOT_PARENT_SENTINELS:
            roots.append(message_id)
            continue
        if not isinstance(parent, str) or parent not in by_id:
            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: broken parent link")
        children[parent].append(message_id)
    if len(roots) != 1:
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: expected one graph root")
    root_id = roots[0]

    state: dict[str, int] = {}
    stack: list[tuple[str, bool]] = [(root_id, False)]
    while stack:
        message_id, exiting = stack.pop()
        if exiting:
            state[message_id] = 2
            continue
        current = state.get(message_id, 0)
        if current == 1:
            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: parent graph cycle")
        if current == 2:
            continue
        state[message_id] = 1
        stack.append((message_id, True))
        stack.extend((child_id, False) for child_id in reversed(children[message_id]))
    if set(state) != set(by_id):
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: disconnected parent graph")

    leaves = sorted(message_id for message_id, child_ids in children.items() if not child_ids)
    paths: list[list[dict]] = []
    for leaf_id in leaves:
        reverse: list[str] = []
        current = leaf_id
        seen: set[str] = set()
        while True:
            if current in seen:
                raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: parent graph cycle")
            seen.add(current)
            reverse.append(current)
            if current == root_id:
                break
            parent = by_id[current].get("parent_message_uuid")
            if not isinstance(parent, str) or parent not in by_id:
                raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: leaf not rooted")
            current = parent
        paths.append([by_id[message_id] for message_id in reversed(reverse)])
    return paths, True


def _branch_session_id(source_id: str, path: list[dict], all_messages: list[dict], branch_count: int) -> tuple[str, list[list[str]]]:
    if branch_count <= 1:
        return source_id, []
    by_parent: dict[str, list[dict]] = {}
    for message in all_messages:
        parent = message.get("parent_message_uuid")
        if isinstance(parent, str) and parent and parent not in _ROOT_PARENT_SENTINELS:
            by_parent.setdefault(parent, []).append(message)
    choices: list[list[str]] = []
    for parent, child in zip(path, path[1:]):
        parent_id = _source_message_id(parent)
        if len(by_parent.get(parent_id, [])) > 1:
            choices.append([parent_id, _source_message_id(child)])
    digest = _sha256(_stable_json_bytes([_source_message_id(m) for m in path]))[:12]
    return f"{source_id}~branch-{digest}", choices


def _mapped_role(sender: object) -> str:
    if sender == "human":
        return "user"
    if sender == "assistant":
        return "assistant"
    return "unknown"


def _trace_projection(message_id: str, sender: object, created: float | None, base_metadata: dict, order: int, content_type: str, payload: dict) -> dict:
    trace_source = {"source_message_uuid": message_id, "sender": sender, **payload}
    trace_id = f"{message_id}~{content_type}-{_sha256(_stable_json_bytes(trace_source))[:12]}"
    return {
        "id": trace_id,
        "author": {"role": "unknown"},
        "create_time": created,
        "content": {"content_type": content_type, **trace_source},
        "metadata": {**base_metadata, "claude_projection": content_type, "claude_source_message_uuid": message_id, "session_search_order": order},
    }


def _convert_message(message: dict, order: int) -> tuple[list[dict], int]:
    message_id = _source_message_id(message)
    sender = message.get("sender")
    role = _mapped_role(sender)
    content = message.get("content")
    if not isinstance(content, list):
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: message content must be list")

    text_parts: list[str] = []
    traces: list[tuple[str, dict]] = []
    for block in content:
        if not isinstance(block, dict):
            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: content block must be object")
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: text block text must be string")
            if text:
                text_parts.append(text)
        elif block_type == "voice_note":
            transcript = block.get("text")
            if transcript not in (None, "") and not isinstance(transcript, str):
                raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: voice_note text must be string")
            if role == "user" and transcript:
                text_parts.append(transcript)
            traces.append(("claude_voice_note", block))
        elif block_type in {"thinking", "tool_use", "tool_result", "token_budget"}:
            traces.append((f"claude_{block_type}", block))
        else:
            traces.append(("claude_unknown_block", block))

    top_text = message.get("text")
    if top_text not in (None, "") and not isinstance(top_text, str):
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: message text must be string")
    if not text_parts and isinstance(top_text, str) and top_text:
        text_parts.append(top_text)

    attachments = message.get("attachments", [])
    files = message.get("files", [])
    if not isinstance(attachments, list) or not isinstance(files, list):
        raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: attachments/files must be lists")
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: attachment must be object")
        traces.append(("claude_attachment", attachment))
    for file_ref in files:
        if not isinstance(file_ref, dict):
            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: file reference must be object")
        traces.append(("claude_file_ref", file_ref))

    created = _message_time(message)
    base_metadata = {"claude_source_sender": sender, "claude_parent_message_uuid": message.get("parent_message_uuid"), "claude_updated_at": message.get("updated_at")}
    converted: list[dict] = []
    if text_parts and role in {"user", "assistant"}:
        has_text_block = any(isinstance(block, dict) and block.get("type") == "text" for block in content)
        converted.append({
            "id": message_id,
            "author": {"role": role},
            "create_time": created,
            "content": {"content_type": "text", "parts": text_parts},
            "metadata": {**base_metadata, "claude_projection": "content-text" if has_text_block else "text-fallback", "session_search_order": order},
        })
        order += 1

    for content_type, block in traces:
        converted.append(_trace_projection(message_id, sender, created, base_metadata, order, content_type, {"block": block}))
        order += 1

    if not converted:
        converted.append(_trace_projection(message_id, sender, created, base_metadata, order, "claude_empty_message", {"flat_text": top_text, "content": content}))
        order += 1
    return converted, order


def _conversation_variants(conversation: dict) -> list[dict]:
    source_id = _source_conversation_id(conversation)
    title = _source_title(conversation)
    messages = _messages(conversation)
    if not messages:
        return []
    paths, graph_mode = _graph_paths(messages)
    variants: list[dict] = []
    for path in paths:
        if graph_mode:
            session_id, branch_choices = _branch_session_id(source_id, path, messages, len(paths))
        else:
            session_id, branch_choices = source_id, []
        output_messages: list[dict] = []
        generated_ids: set[str] = set()
        order = 0
        for message in path:
            converted, order = _convert_message(message, order)
            for item in converted:
                if item["id"] in generated_ids:
                    raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: generated message id collision")
                generated_ids.add(item["id"])
                output_messages.append(item)
        variants.append({
            "conversation_id": session_id,
            "title": title,
            "messages": output_messages,
            "page_info": {"has_previous_page": True, "has_next_page": False},
            "claude_source_conversation_id": source_id,
            "claude_selected_leaf_id": _source_message_id(path[-1]) if path else None,
            "claude_branch_choices": branch_choices,
            "claude_branch_count": len(paths),
            "claude_graph_mode": graph_mode,
            "claude_snapshot_scope": SNAPSHOT_SCOPE,
            "claude_created_at": conversation.get("created_at"),
            "claude_updated_at": conversation.get("updated_at"),
        })
    return variants


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, _FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def _portable_zip_bytes(member: str, payload_bytes: bytes, manifest: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        _zip_write(zf, member, payload_bytes)
        _zip_write(zf, "manifest.json", _stable_json_bytes(manifest))
    return buffer.getvalue()


def _publish_content_addressed(target: pathlib.Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: content-address collision")
        return
    temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: concurrent content-address collision")
            return
        os.replace(temp, target)
        if target.read_bytes() != data:
            raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: published artifact mismatch")
    finally:
        if temp.exists():
            temp.unlink()


def _materialize_export_snapshot(source: pathlib.Path, output_dir: pathlib.Path) -> _MaterializedSnapshot:
    source = pathlib.Path(source)
    output_dir = pathlib.Path(output_dir)
    raw, rows, parser_mode = _load_source(source)
    parent_sha = _sha256(raw)
    seen_source_ids: set[str] = set()
    seen_session_ids: set[str] = set()
    outputs: list[pathlib.Path] = []
    pending: list[tuple[pathlib.Path, pathlib.Path]] = []
    branch_count = 0
    staging_root = pathlib.Path(tempfile.mkdtemp(prefix="session-search-claude-stage-"))
    try:
        ordered = sorted(rows, key=lambda row: _source_conversation_id(row[1]))
        for source_member, conversation in ordered:
            source_id = _source_conversation_id(conversation)
            if source_id in seen_source_ids:
                raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: duplicate conversation uuid")
            seen_source_ids.add(source_id)
            created = conversation.get("created_at")
            updated = conversation.get("updated_at")
            if created not in (None, ""):
                _parse_time(created)
            if updated not in (None, ""):
                _parse_time(updated)
            variants = _conversation_variants(conversation)
            branch_count += len(variants)
            for payload in variants:
                session_id = payload["conversation_id"]
                if session_id in seen_session_ids:
                    raise ValueError("BLOCKED_UNSUPPORTED_CLAUDE_EXPORT: duplicate materialized session id")
                seen_session_ids.add(session_id)
                sanitized = _SAFE_ID.sub("_", session_id).strip("._") or "session"
                id_hash = _sha256(session_id.encode("utf-8"))[:12]
                safe_id = f"{sanitized}-{id_hash}"
                member = f"optional/conversation-claude-{safe_id}.bin"
                payload_bytes = _stable_json_bytes(payload)
                manifest = {
                    "schema": SCHEMA,
                    "source_adapter": ADAPTER,
                    "source_export_sha256": parent_sha,
                    "source_export_bytes": len(raw),
                    "source_member": source_member,
                    "source_conversation_id": source_id,
                    "session_id": session_id,
                    "branch_leaf_id": payload["claude_selected_leaf_id"],
                    "branch_count": payload["claude_branch_count"],
                    "files": [{"name": member, "bytes": len(payload_bytes), "sha256": _sha256(payload_bytes)}],
                }
                archive_bytes = _portable_zip_bytes(member, payload_bytes, manifest)
                archive_sha = _sha256(archive_bytes)
                target = output_dir / f"claude-{safe_id}-{archive_sha[:16]}.zip"
                stage_path = staging_root / f"{len(pending):08d}.zip"
                stage_path.write_bytes(archive_bytes)
                pending.append((target, stage_path))
                outputs.append(target)

        output_dir.mkdir(parents=True, exist_ok=True)
        for target, stage_path in pending:
            _publish_content_addressed(target, stage_path.read_bytes())
        return _MaterializedSnapshot(tuple(outputs), parent_sha, len(rows), branch_count, parser_mode)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def materialize_export(source: pathlib.Path, output_dir: pathlib.Path) -> list[pathlib.Path]:
    return list(_materialize_export_snapshot(source, output_dir).artifacts)


def ingest_export(source: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    from .corpus_store import ingest_many

    source = pathlib.Path(source)
    with tempfile.TemporaryDirectory(prefix="session-search-claude-") as td:
        snapshot = _materialize_export_snapshot(source, pathlib.Path(td))
        child_ids = {str(path): f"claude-child-sha256:{_sha256(path.read_bytes())}" for path in snapshot.artifacts}
        result = ingest_many(list(snapshot.artifacts), pathlib.Path(corpus_root))
        for item in result.get("results", []):
            source_value = item.get("source")
            if source_value in child_ids:
                item["source"] = child_ids[source_value]
    return {
        **result,
        "conversation_count": snapshot.conversation_count,
        "artifact_count": len(snapshot.artifacts),
        "branch_count": snapshot.branch_count,
        "parser_mode": snapshot.parser_mode,
        "source_export_sha256": snapshot.source_export_sha256,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize an official Claude conversations export into portable Session Search artifacts.")
    parser.add_argument("source", type=pathlib.Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output-dir", type=pathlib.Path)
    destination.add_argument("--corpus", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.corpus is not None:
            result = ingest_export(args.source, args.corpus)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result.get("status") == "COMPLETE" else 1
        outputs = materialize_export(args.source, args.output_dir)
    except Exception as exc:
        print(f"CLAUDE EXPORT FAILED: {exc}")
        return 1
    print(json.dumps({"artifacts": [str(path) for path in outputs], "count": len(outputs)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
