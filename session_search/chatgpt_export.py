from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import json
import os
import pathlib
import re
import tempfile
import uuid
import zipfile
from typing import Iterator

SCHEMA = "theseus.session-search.chatgpt-export-child.v1"
ADAPTER = "chatgpt-export"
SNAPSHOT_SCOPE = "SNAPSHOT_EXPOSED_BRANCHES"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_SAFE_ID = re.compile(r"[^A-Za-z0-9._~-]+")
_CONVERSATIONS_MEMBER = re.compile(r"^conversations-(\d+)\.json$")
_SINGLE_CONVERSATIONS_MEMBER = "conversations.json"


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


def _file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with pathlib.Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _conversation_members(zf: zipfile.ZipFile) -> list[str]:
    found: list[tuple[int, str]] = []
    direct: list[str] = []
    for raw_name in zf.namelist():
        p = pathlib.PurePosixPath(raw_name)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: unsafe ZIP member")
        if p.name == _SINGLE_CONVERSATIONS_MEMBER:
            direct.append(raw_name)
            continue
        match = _CONVERSATIONS_MEMBER.fullmatch(p.name)
        if match:
            found.append((int(match.group(1)), raw_name))
    if direct and found:
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: ambiguous conversations members")
    if len(direct) > 1:
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: duplicate conversations member")
    if direct:
        return direct
    if not found:
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: conversations JSON members missing")
    found.sort()
    indexes = [index for index, _ in found]
    if len(indexes) != len(set(indexes)):
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: duplicate conversations member index")
    return [name for _, name in found]


def _iter_member_conversations(zf: zipfile.ZipFile, member: str) -> tuple[str, Iterator[dict]]:
    try:
        import ijson  # type: ignore
    except ImportError:
        def fallback() -> Iterator[dict]:
            try:
                with zf.open(member) as fh:
                    obj = json.load(io.TextIOWrapper(fh, encoding="utf-8"))
            except Exception as exc:
                raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: invalid conversations JSON") from exc
            if not isinstance(obj, list):
                raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: conversations member must be array")
            for item in obj:
                if not isinstance(item, dict):
                    raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: conversation must be object")
                yield item
        return "stdlib-json", fallback()

    def streaming() -> Iterator[dict]:
        try:
            with zf.open(member) as fh:
                for item in ijson.items(fh, "item", use_float=True):
                    if not isinstance(item, dict):
                        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: conversation must be object")
                    yield item
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: invalid conversations JSON") from exc
    return "ijson-stream", streaming()


def _source_conversation_id(conversation: dict) -> str:
    cid = conversation.get("conversation_id")
    legacy = conversation.get("id")
    values = [value for value in (cid, legacy) if value is not None]
    if not values or not all(isinstance(value, str) and value for value in values):
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: conversation id missing")
    if len(set(values)) != 1:
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: conversation identity mismatch")
    return str(values[0])


def _message_time(node: dict) -> float | None:
    message = node.get("message")
    if not isinstance(message, dict):
        return None
    value = message.get("create_time")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: invalid message timestamp")
    return float(value)


def _mapping_graph(conversation: dict) -> tuple[dict[str, dict], str, dict[str, list[str]], list[str], str]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: mapping missing")
    by_id: dict[str, dict] = {}
    for raw_id, node in mapping.items():
        node_id = str(raw_id)
        if not isinstance(node, dict):
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: mapping node must be object")
        embedded = node.get("id")
        if embedded is not None and str(embedded) != node_id:
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: node identity mismatch")
        if node_id in by_id:
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: duplicate node id")
        by_id[node_id] = node

    children: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    roots: list[str] = []
    for node_id, node in by_id.items():
        parent = node.get("parent")
        if parent is None:
            roots.append(node_id)
            continue
        if not isinstance(parent, str) or parent not in by_id:
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: broken parent link")
        children[parent].append(node_id)
    if len(roots) != 1:
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: expected one root")
    root_id = roots[0]

    for node_id, node in by_id.items():
        if "children" not in node:
            continue
        hint = node.get("children")
        if not isinstance(hint, list) or any(not isinstance(value, str) for value in hint):
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: children hint malformed")
        if set(hint) != set(children[node_id]) or len(hint) != len(children[node_id]):
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: children hint inconsistent with parent authority")

    state: dict[str, int] = {}
    stack: list[tuple[str, bool]] = [(root_id, False)]
    while stack:
        node_id, exiting = stack.pop()
        if exiting:
            state[node_id] = 2
            continue
        current = state.get(node_id, 0)
        if current == 1:
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: mapping cycle")
        if current == 2:
            continue
        state[node_id] = 1
        stack.append((node_id, True))
        stack.extend((child_id, False) for child_id in reversed(children[node_id]))
    if set(state) != set(by_id):
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: disconnected mapping")

    leaves = sorted(node_id for node_id, child_ids in children.items() if not child_ids)
    if not leaves:
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: mapping has no leaf")
    current_node = conversation.get("current_node")
    if not isinstance(current_node, str) or current_node not in by_id:
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: current_node invalid")
    if children[current_node]:
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: current_node must be terminal")
    return by_id, root_id, children, leaves, current_node


def _path_to_root(by_id: dict[str, dict], root_id: str, leaf_id: str) -> list[str]:
    reverse: list[str] = []
    seen: set[str] = set()
    current: str | None = leaf_id
    while current is not None:
        if current in seen:
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: mapping cycle")
        seen.add(current)
        node = by_id.get(current)
        if node is None:
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: broken path")
        reverse.append(current)
        if current == root_id:
            break
        parent = node.get("parent")
        if not isinstance(parent, str):
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: leaf not rooted")
        current = parent
    path = list(reversed(reverse))
    if not path or path[0] != root_id:
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: leaf not rooted")
    return path


def _default_child(parent_id: str, siblings: list[str], by_id: dict[str, dict]) -> str:
    if len(siblings) == 1:
        return siblings[0]
    observed: list[tuple[float, str]] = []
    for child_id in siblings:
        value = _message_time(by_id[child_id])
        if value is None:
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: ambiguous branch without child timestamps")
        observed.append((value, child_id))
    times = [value for value, _ in observed]
    if len(set(times)) != len(times):
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: ambiguous branch with tied child timestamps")
    return min(observed)[1]


def _branch_session_id(source_id: str, path_ids: list[str], children: dict[str, list[str]], by_id: dict[str, dict]) -> tuple[str, list[list[str]]]:
    choices: list[list[str]] = []
    for parent_id, child_id in zip(path_ids, path_ids[1:]):
        siblings = children[parent_id]
        if len(siblings) > 1 and child_id != _default_child(parent_id, siblings, by_id):
            choices.append([parent_id, child_id])
    if not choices:
        return source_id, choices
    digest = _sha256(_stable_json_bytes(choices))[:12]
    return f"{source_id}~branch-{digest}", choices


def _mapped_role(value: object) -> str:
    role = str(value or "unknown")
    if role in {"user", "assistant", "system", "tool"}:
        return role
    return "unknown"


def _source_message_id(node_id: str, message: dict) -> str:
    value = message.get("id")
    if isinstance(value, str) and value:
        return value
    return f"chatgpt-node-v1:{node_id}"


def _multimodal_parts(content: dict) -> tuple[list[object], list[object]]:
    parts = content.get("parts")
    if not isinstance(parts, list):
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: multimodal parts must be list")
    text_parts: list[object] = []
    trace_parts: list[object] = []
    for part in parts:
        if isinstance(part, (str, int, float)) and not isinstance(part, bool):
            text_parts.append(part)
            continue
        if isinstance(part, dict) and part.get("content_type") == "audio_transcription" and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
        trace_parts.append(part)
    return text_parts, trace_parts


def _convert_node_messages(node_id: str, node: dict, order: int) -> tuple[list[dict], int]:
    raw = node.get("message")
    if raw is None:
        if node.get("parent") is None:
            return [], order
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: non-root message missing")
    if not isinstance(raw, dict):
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: message must be object")
    author = raw.get("author")
    content = raw.get("content")
    if not isinstance(author, dict) or not isinstance(content, dict):
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: message author/content malformed")
    role = _mapped_role(author.get("role"))
    content_type = str(content.get("content_type") or "unknown")
    message_id = _source_message_id(node_id, raw)
    created = _message_time(node)
    source_metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    base_metadata = {
        "chatgpt_node_id": node_id,
        "chatgpt_parent_node_id": node.get("parent"),
        "chatgpt_source_role": author.get("role"),
        "chatgpt_source_content_type": content_type,
        "chatgpt_source_metadata": source_metadata,
    }

    if content_type == "multimodal_text" and role in {"user", "assistant"}:
        text_parts, trace_parts = _multimodal_parts(content)
        converted: list[dict] = []
        if text_parts:
            converted.append({
                "id": message_id,
                "author": {"role": role},
                "create_time": created,
                "content": {"content_type": "text", "parts": text_parts},
                "metadata": {**base_metadata, "chatgpt_projection": "multimodal-text", "session_search_order": order},
            })
            order += 1
        if trace_parts:
            trace_id = f"{message_id}~multimodal-trace-{_sha256(_stable_json_bytes(trace_parts))[:12]}"
            converted.append({
                "id": trace_id,
                "author": {"role": "unknown"},
                "create_time": created,
                "content": {"content_type": "chatgpt_multimodal_trace", "parts": trace_parts, "source_content": content},
                "metadata": {**base_metadata, "chatgpt_projection": "multimodal-trace", "chatgpt_source_message_id": message_id, "session_search_order": order},
            })
            order += 1
        if converted:
            return converted, order

    return [{
        "id": message_id,
        "author": {"role": role},
        "create_time": created,
        "content": content,
        "metadata": {**base_metadata, "session_search_order": order},
    }], order + 1


def _conversation_variants(conversation: dict) -> list[dict]:
    source_id = _source_conversation_id(conversation)
    title = conversation.get("title")
    if not isinstance(title, str):
        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: conversation title missing")
    by_id, root_id, children, leaves, current_node = _mapping_graph(conversation)
    variants: list[dict] = []
    for leaf_id in leaves:
        path_ids = _path_to_root(by_id, root_id, leaf_id)
        session_id, branch_choices = _branch_session_id(source_id, path_ids, children, by_id)
        messages: list[dict] = []
        generated_ids: set[str] = set()
        order = 0
        for node_id in path_ids:
            converted, order = _convert_node_messages(node_id, by_id[node_id], order)
            for message in converted:
                if message["id"] in generated_ids:
                    raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: generated message id collision")
                generated_ids.add(message["id"])
                messages.append(message)
        variants.append({
            "conversation_id": session_id,
            "title": title,
            "messages": messages,
            "page_info": {"has_previous_page": True, "has_next_page": False},
            "chatgpt_source_conversation_id": source_id,
            "chatgpt_selected_leaf_id": leaf_id,
            "chatgpt_current_node_id": current_node,
            "chatgpt_selected_is_current": leaf_id == current_node,
            "chatgpt_branch_choices": branch_choices,
            "chatgpt_branch_count": len(leaves),
            "chatgpt_snapshot_scope": SNAPSHOT_SCOPE,
            "chatgpt_create_time": conversation.get("create_time"),
            "chatgpt_update_time": conversation.get("update_time"),
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
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: content-address collision")
        return
    temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: concurrent content-address collision")
            return
        os.replace(temp, target)
        if target.read_bytes() != data:
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: published artifact mismatch")
    finally:
        if temp.exists():
            temp.unlink()


def _materialize_export_snapshot(source: pathlib.Path, output_dir: pathlib.Path) -> _MaterializedSnapshot:
    source = pathlib.Path(source)
    output_dir = pathlib.Path(output_dir)
    parent_sha = _file_sha256(source)
    source_size = source.stat().st_size
    outputs: list[pathlib.Path] = []
    pending: list[tuple[pathlib.Path, pathlib.Path]] = []
    seen_source_ids: set[str] = set()
    seen_session_ids: set[str] = set()
    conversation_count = 0
    branch_count = 0
    parser_modes: set[str] = set()

    staging_root = pathlib.Path(tempfile.mkdtemp(prefix="session-search-chatgpt-stage-"))
    try:
        try:
            zf = zipfile.ZipFile(source)
        except Exception as exc:
            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: invalid ZIP") from exc
        with zf:
            members = _conversation_members(zf)
            for member in members:
                parser_mode, conversations = _iter_member_conversations(zf, member)
                parser_modes.add(parser_mode)
                for conversation in conversations:
                    conversation_count += 1
                    source_id = _source_conversation_id(conversation)
                    if source_id in seen_source_ids:
                        raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: duplicate conversation id")
                    seen_source_ids.add(source_id)
                    variants = _conversation_variants(conversation)
                    branch_count += len(variants)
                    for payload in variants:
                        session_id = payload["conversation_id"]
                        if session_id in seen_session_ids:
                            raise ValueError("BLOCKED_UNSUPPORTED_CHATGPT_EXPORT: duplicate materialized session id")
                        seen_session_ids.add(session_id)
                        sanitized = _SAFE_ID.sub("_", session_id).strip("._") or "session"
                        id_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
                        safe_id = f"{sanitized}-{id_hash}"
                        child_member = f"optional/conversation-chatgpt-{safe_id}.bin"
                        payload_bytes = _stable_json_bytes(payload)
                        manifest = {
                            "schema": SCHEMA,
                            "source_adapter": ADAPTER,
                            "source_export_sha256": parent_sha,
                            "source_export_bytes": source_size,
                            "source_conversations_member": pathlib.PurePosixPath(member).name,
                            "source_conversation_id": source_id,
                            "session_id": session_id,
                            "branch_leaf_id": payload["chatgpt_selected_leaf_id"],
                            "branch_count": payload["chatgpt_branch_count"],
                            "selected_is_current": payload["chatgpt_selected_is_current"],
                            "snapshot_scope": SNAPSHOT_SCOPE,
                            "files": [{"name": child_member, "bytes": len(payload_bytes), "sha256": _sha256(payload_bytes)}],
                        }
                        archive_bytes = _portable_zip_bytes(child_member, payload_bytes, manifest)
                        archive_sha = _sha256(archive_bytes)
                        target = output_dir / f"chatgpt-{safe_id}-{archive_sha[:16]}.zip"
                        stage_path = staging_root / f"{len(pending):08d}.zip"
                        stage_path.write_bytes(archive_bytes)
                        pending.append((target, stage_path))
                        outputs.append(target)

        output_dir.mkdir(parents=True, exist_ok=True)
        for target, stage_path in pending:
            _publish_content_addressed(target, stage_path.read_bytes())
    finally:
        import shutil
        shutil.rmtree(staging_root, ignore_errors=True)
    parser_mode = "+".join(sorted(parser_modes)) or "UNKNOWN"
    return _MaterializedSnapshot(tuple(outputs), parent_sha, conversation_count, branch_count, parser_mode)


def materialize_export(source: pathlib.Path, output_dir: pathlib.Path) -> list[pathlib.Path]:
    return list(_materialize_export_snapshot(source, output_dir).artifacts)


def ingest_export(source: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    from .corpus_store import ingest_many

    with tempfile.TemporaryDirectory(prefix="session-search-chatgpt-") as td:
        snapshot = _materialize_export_snapshot(pathlib.Path(source), pathlib.Path(td))
        child_ids = {str(path): f"chatgpt-child-sha256:{_sha256(path.read_bytes())}" for path in snapshot.artifacts}
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
        "source_export_sha256": snapshot.source_export_sha256,
        "snapshot_scope": SNAPSHOT_SCOPE,
        "parser_mode": snapshot.parser_mode,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize an official ChatGPT data-export ZIP into portable Session Search branch artifacts.")
    parser.add_argument("source", type=pathlib.Path)
    dest = parser.add_mutually_exclusive_group(required=True)
    dest.add_argument("--output-dir", type=pathlib.Path)
    dest.add_argument("--corpus", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.corpus is not None:
            result = ingest_export(args.source, args.corpus)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result.get("status") == "COMPLETE" else 1
        outputs = materialize_export(args.source, args.output_dir)
    except Exception as exc:
        print(f"CHATGPT EXPORT FAILED: {exc}")
        return 1
    print(json.dumps({"artifacts": [str(path) for path in outputs], "count": len(outputs)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
