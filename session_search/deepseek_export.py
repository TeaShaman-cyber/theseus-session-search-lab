from __future__ import annotations

import argparse
import base64
import dataclasses
import io
import hashlib
import json
import pathlib
import re
import tempfile
import zipfile
from datetime import datetime

SCHEMA = "theseus.session-search.deepseek-export-child.v1"
ADAPTER = "deepseek-export"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _stable_json_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_time(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: timestamp must be timezone-aware")
    return parsed.timestamp()


@dataclasses.dataclass(frozen=True)
class _MaterializedSnapshot:
    artifacts: tuple[pathlib.Path, ...]
    source_export_sha256: str
    conversation_count: int


def _mapping_paths(mapping: dict) -> list[tuple[str, list[dict]]]:
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: mapping missing")
    roots = [node for node in mapping.values() if isinstance(node, dict) and node.get("parent") is None]
    if len(roots) != 1:
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: expected one root")
    root = roots[0]
    root_id = str(root.get("id") or "")
    if not root_id or mapping.get(root_id) is not root:
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: root identity mismatch")

    for node_id, node in mapping.items():
        if not isinstance(node, dict) or str(node.get("id") or "") != str(node_id):
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: node identity mismatch")
        if "children" not in node or not isinstance(node.get("children"), list):
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: children must be list")
        if node_id != root_id and not isinstance(node.get("parent"), str):
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: non-root parent missing")
        for child_id_raw in node["children"]:
            child_id = str(child_id_raw)
            child = mapping.get(child_id)
            if not isinstance(child, dict) or str(child.get("parent") or "") != str(node_id):
                raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: broken parent link")

    state: dict[str, int] = {}
    stack: list[tuple[str, bool]] = [(root_id, False)]
    while stack:
        node_id, exiting = stack.pop()
        if exiting:
            state[node_id] = 2
            continue
        current = state.get(node_id, 0)
        if current == 1:
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: mapping cycle")
        if current == 2:
            continue
        state[node_id] = 1
        stack.append((node_id, True))
        for child_id_raw in reversed(mapping[node_id]["children"]):
            stack.append((str(child_id_raw), False))
    if set(state) != set(mapping):
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: disconnected mapping")

    leaves = sorted(str(node_id) for node_id, node in mapping.items() if not node["children"])
    if not leaves:
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: mapping has no leaf")
    paths: list[tuple[str, list[dict]]] = []
    for leaf_id in leaves:
        reversed_ids: list[str] = []
        current: str | None = leaf_id
        seen: set[str] = set()
        while current is not None:
            if current in seen:
                raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: mapping cycle")
            seen.add(current)
            node = mapping.get(current)
            if not isinstance(node, dict):
                raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: broken path")
            reversed_ids.append(current)
            parent = node.get("parent")
            current = str(parent) if parent is not None else None
        path_ids = list(reversed(reversed_ids))
        if not path_ids or path_ids[0] != root_id:
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: leaf not rooted")
        paths.append((leaf_id, [mapping[node_id] for node_id in path_ids]))
    return paths


def _fragment_message_id(node_id: str, fragment_index: int) -> str:
    raw = _stable_json_bytes([node_id, int(fragment_index)])
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"deepseek-fragment-v1:{encoded}"


def _fragment_message(node: dict, fragment: dict, fragment_index: int, create_time: float | None, order: int) -> dict:
    kind = str(fragment.get("type") or "")
    node_id = str(node.get("id") or "")
    message_id = _fragment_message_id(node_id, fragment_index)
    if kind == "REQUEST":
        role, search_content = "user", str(fragment.get("content") or "")
    elif kind == "RESPONSE":
        role, search_content = "assistant", str(fragment.get("content") or "")
    elif kind == "FILE":
        role = "tool"
        files = fragment.get("files") or []
        search_content = "\n".join(
            str(item.get("file_name") or "") for item in files if isinstance(item, dict) and item.get("file_name")
        )
    else:
        content = fragment.get("content")
        role = "unknown"
        search_content = "" if content is None else str(content)
    message = node.get("message") or {}
    return {
        "id": message_id,
        "author": {"role": role},
        "create_time": create_time,
        "content": {"content_type": "text", "parts": [search_content]},
        "metadata": {
            "deepseek_fragment_type": kind,
            "deepseek_inserted_at": message.get("inserted_at"),
            "deepseek_model": message.get("model"),
            "deepseek_node_id": node_id,
            "deepseek_fragment_index": fragment_index,
            "session_search_order": order,
        },
    }


def _empty_fragment_placeholder(node: dict, create_time: float | None, order: int) -> dict:
    node_id = str(node.get("id") or "")
    message = node.get("message") or {}
    return {
        "id": _fragment_message_id(node_id, -1),
        "author": {"role": "unknown"},
        "create_time": create_time,
        "content": {"content_type": "deepseek_empty_fragments", "parts": []},
        "metadata": {
            "deepseek_fragment_type": "EMPTY_COLLECTION",
            "deepseek_inserted_at": message.get("inserted_at"),
            "deepseek_model": message.get("model"),
            "deepseek_node_id": node_id,
            "session_search_order": order,
        },
    }


def _conversation_payload(conversation: dict, nodes: list[dict], leaf_id: str, branch_count: int) -> dict:
    source_session_id = conversation.get("id")
    title = conversation.get("title")
    if not isinstance(source_session_id, str) or not source_session_id or not isinstance(title, str):
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: conversation identity/title missing")
    branch_choices = []
    for parent, child in zip(nodes, nodes[1:]):
        children = [str(value) for value in (parent.get("children") or [])]
        if len(children) > 1:
            selected = str(child.get("id") or "")
            if selected != children[0]:
                branch_choices.append([str(parent.get("id") or ""), selected])
    session_id = source_session_id
    if branch_choices:
        branch_hash = hashlib.sha256(_stable_json_bytes(branch_choices)).hexdigest()[:12]
        session_id = f"{source_session_id}~branch-{branch_hash}"

    messages = []
    generated_ids: set[str] = set()
    order = 0
    for node in nodes:
        raw_message = node.get("message")
        if raw_message is None:
            if node.get("parent") is None:
                continue
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: non-root message missing")
        if not isinstance(raw_message, dict):
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: message must be object")
        if "fragments" not in raw_message or not isinstance(raw_message.get("fragments"), list):
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: fragments must be list")
        fragments = raw_message["fragments"]
        base = _parse_time(raw_message.get("inserted_at"))
        if not fragments:
            placeholder = _empty_fragment_placeholder(node, base, order)
            order += 1
            if placeholder["id"] in generated_ids:
                raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: generated message id collision")
            generated_ids.add(placeholder["id"])
            messages.append(placeholder)
            continue
        for fragment_index, fragment in enumerate(fragments):
            if not isinstance(fragment, dict):
                raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: fragment must be object")
            create_time = base
            converted = _fragment_message(node, fragment, fragment_index, create_time, order)
            order += 1
            if converted["id"] in generated_ids:
                raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: generated message id collision")
            generated_ids.add(converted["id"])
            messages.append(converted)
    return {
        "conversation_id": session_id,
        "title": title,
        "messages": messages,
        "page_info": {"has_previous_page": False, "has_next_page": False},
        "deepseek_source_conversation_id": source_session_id,
        "deepseek_leaf_node_id": leaf_id,
        "deepseek_branch_count": branch_count,
        "deepseek_inserted_at": conversation.get("inserted_at"),
        "deepseek_updated_at": conversation.get("updated_at"),
    }


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


def _materialize_export_snapshot(source: pathlib.Path, output_dir: pathlib.Path) -> _MaterializedSnapshot:
    source = pathlib.Path(source)
    output_dir = pathlib.Path(output_dir)
    raw = source.read_bytes()
    parent_sha = _sha256(raw)
    try:
        conversations = json.loads(raw)
    except Exception as exc:
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: invalid JSON") from exc
    if not isinstance(conversations, list):
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: top level must be list")
    outputs: list[pathlib.Path] = []
    pending: list[tuple[pathlib.Path, bytes]] = []
    seen_source_ids: set[str] = set()
    seen_session_ids: set[str] = set()
    for conversation in sorted(conversations, key=lambda item: str(item.get("id") if isinstance(item, dict) else "")):
        if not isinstance(conversation, dict):
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: conversation must be object")
        source_session_id = conversation.get("id")
        if not isinstance(source_session_id, str) or not source_session_id:
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: conversation identity/title missing")
        if source_session_id in seen_source_ids:
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: duplicate conversation id")
        seen_source_ids.add(source_session_id)
        paths = _mapping_paths(conversation.get("mapping"))
        branch_count = len(paths)
        for leaf_id, nodes in paths:
            payload = _conversation_payload(conversation, nodes, leaf_id, branch_count)
            session_id = payload["conversation_id"]
            if session_id in seen_session_ids:
                raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: duplicate materialized session id")
            seen_session_ids.add(session_id)
            sanitized = _SAFE_ID.sub("_", session_id).strip("._") or "session"
            id_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
            safe_id = f"{sanitized}-{id_hash}"
            member = f"optional/conversation-deepseek-{safe_id}.bin"
            payload_bytes = _stable_json_bytes(payload)
            manifest = {
                "schema": SCHEMA,
                "source_adapter": ADAPTER,
                "source_export_sha256": parent_sha,
                "source_export_bytes": len(raw),
                "source_conversation_id": source_session_id,
                "session_id": session_id,
                "branch_leaf_id": leaf_id,
                "branch_count": branch_count,
                "files": [{"name": member, "bytes": len(payload_bytes), "sha256": _sha256(payload_bytes)}],
            }
            archive_bytes = _portable_zip_bytes(member, payload_bytes, manifest)
            archive_sha = _sha256(archive_bytes)
            target = output_dir / f"deepseek-{safe_id}-{archive_sha[:16]}.zip"
            pending.append((target, archive_bytes))
            outputs.append(target)

    output_dir.mkdir(parents=True, exist_ok=True)
    for target, archive_bytes in pending:
        if target.exists():
            if target.read_bytes() != archive_bytes:
                raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: content-address collision")
            continue
        try:
            with target.open("xb") as handle:
                handle.write(archive_bytes)
        except FileExistsError:
            if target.read_bytes() != archive_bytes:
                raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: concurrent content-address collision")
    return _MaterializedSnapshot(tuple(outputs), parent_sha, len(conversations))


def materialize_export(source: pathlib.Path, output_dir: pathlib.Path) -> list[pathlib.Path]:
    return list(_materialize_export_snapshot(source, output_dir).artifacts)


def ingest_export(source: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    from .corpus_store import ingest_many

    source = pathlib.Path(source)
    with tempfile.TemporaryDirectory(prefix="session-search-deepseek-") as td:
        snapshot = _materialize_export_snapshot(source, pathlib.Path(td))
        child_ids = {str(path): f"deepseek-child-sha256:{_sha256(path.read_bytes())}" for path in snapshot.artifacts}
        result = ingest_many(list(snapshot.artifacts), pathlib.Path(corpus_root))
        for item in result.get("results", []):
            source_value = item.get("source")
            if source_value in child_ids:
                item["source"] = child_ids[source_value]
    return {
        **result,
        "conversation_count": snapshot.conversation_count,
        "artifact_count": len(snapshot.artifacts),
        "source_export_sha256": snapshot.source_export_sha256,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Materialize official DeepSeek conversations.json into portable per-session Session Search artifacts.")
    p.add_argument("source", type=pathlib.Path)
    dest = p.add_mutually_exclusive_group(required=True)
    dest.add_argument("--output-dir", type=pathlib.Path)
    dest.add_argument("--corpus", type=pathlib.Path)
    args = p.parse_args(argv)
    try:
        if args.corpus is not None:
            result = ingest_export(args.source, args.corpus)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result.get("status") == "COMPLETE" else 1
        outputs = materialize_export(args.source, args.output_dir)
    except Exception as exc:
        print(f"DEEPSEEK EXPORT FAILED: {exc}")
        return 1
    print(json.dumps({"artifacts": [str(path) for path in outputs], "count": len(outputs)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
