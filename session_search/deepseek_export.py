from __future__ import annotations

import argparse
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
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _ordered_nodes(mapping: dict) -> list[dict]:
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: mapping missing")
    roots = [node for node in mapping.values() if isinstance(node, dict) and node.get("parent") is None]
    if len(roots) != 1:
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: expected one root")
    root = roots[0]
    root_id = str(root.get("id") or "")
    if not root_id or mapping.get(root_id) is not root:
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: root identity mismatch")

    ordered: list[dict] = []
    state: dict[str, int] = {}
    stack: list[tuple[str, bool]] = [(root_id, False)]
    while stack:
        node_id, exiting = stack.pop()
        node = mapping.get(node_id)
        if not isinstance(node, dict) or str(node.get("id") or "") != node_id:
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: child identity mismatch")
        if exiting:
            state[node_id] = 2
            continue
        current = state.get(node_id, 0)
        if current == 1:
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: mapping cycle")
        if current == 2:
            continue
        state[node_id] = 1
        if node.get("message") is not None:
            ordered.append(node)
        children = node.get("children") or []
        if not isinstance(children, list):
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: children must be list")
        stack.append((node_id, True))
        for child_id_raw in reversed(children):
            child_id = str(child_id_raw)
            child = mapping.get(child_id)
            if not isinstance(child, dict) or str(child.get("parent") or "") != node_id:
                raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: broken parent link")
            stack.append((child_id, False))
    if set(state) != set(mapping):
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: disconnected mapping")
    return ordered

def _fragment_message(node: dict, fragment: dict, fragment_index: int, create_time: float) -> dict | None:
    kind = str(fragment.get("type") or "")
    node_id = str(node.get("id") or "")
    message_id = node_id if fragment_index == 0 else f"{node_id}:{fragment_index}"
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
        if content is None:
            return None
        role, search_content = "unknown", str(content)
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
        },
    }


def _conversation_payload(conversation: dict) -> dict:
    session_id = conversation.get("id")
    title = conversation.get("title")
    mapping = conversation.get("mapping")
    if not isinstance(session_id, str) or not session_id or not isinstance(title, str):
        raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: conversation identity/title missing")
    nodes = _ordered_nodes(mapping)
    messages = []
    last_time: float | None = None
    for sequence, node in enumerate(nodes):
        message = node.get("message") or {}
        fragments = message.get("fragments") or []
        if not isinstance(fragments, list):
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: fragments must be list")
        observed = _parse_time(message.get("inserted_at"))
        base = observed if observed is not None else float(sequence)
        if last_time is not None and base <= last_time:
            base = last_time + 0.000001
        for fragment_index, fragment in enumerate(fragments):
            if not isinstance(fragment, dict):
                raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: fragment must be object")
            create_time = base + fragment_index * 0.0000001
            converted = _fragment_message(node, fragment, fragment_index, create_time)
            if converted is not None:
                messages.append(converted)
                last_time = create_time
        if last_time is None:
            last_time = base
    return {
        "conversation_id": session_id,
        "title": title,
        "messages": messages,
        "page_info": {"has_previous_page": False, "has_next_page": False},
        "deepseek_inserted_at": conversation.get("inserted_at"),
        "deepseek_updated_at": conversation.get("updated_at"),
    }


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, _FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def materialize_export(source: pathlib.Path, output_dir: pathlib.Path) -> list[pathlib.Path]:
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
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    seen: set[str] = set()
    for conversation in sorted(conversations, key=lambda item: str(item.get("id") if isinstance(item, dict) else "")):
        if not isinstance(conversation, dict):
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: conversation must be object")
        payload = _conversation_payload(conversation)
        session_id = payload["conversation_id"]
        if session_id in seen:
            raise ValueError("BLOCKED_UNSUPPORTED_DEEPSEEK_EXPORT: duplicate conversation id")
        seen.add(session_id)
        safe_id = _SAFE_ID.sub("_", session_id).strip("._") or hashlib.sha256(session_id.encode()).hexdigest()[:16]
        member = f"optional/conversation-deepseek-{safe_id}.bin"
        payload_bytes = _stable_json_bytes(payload)
        manifest = {
            "schema": SCHEMA,
            "source_adapter": ADAPTER,
            "source_export_sha256": parent_sha,
            "source_export_bytes": len(raw),
            "session_id": session_id,
            "files": [{"name": member, "bytes": len(payload_bytes), "sha256": _sha256(payload_bytes)}],
        }
        target = output_dir / f"deepseek-{safe_id}.zip"
        with zipfile.ZipFile(target, "w") as zf:
            _zip_write(zf, member, payload_bytes)
            _zip_write(zf, "manifest.json", _stable_json_bytes(manifest))
        outputs.append(target)
    return outputs



def ingest_export(source: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    from .corpus_store import ingest_many

    source = pathlib.Path(source)
    with tempfile.TemporaryDirectory(prefix="session-search-deepseek-") as td:
        children = materialize_export(source, pathlib.Path(td))
        result = ingest_many(children, pathlib.Path(corpus_root))
    return {**result, "conversation_count": len(children), "source_export_sha256": _sha256(source.read_bytes())}

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
