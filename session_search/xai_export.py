from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import json
import os
import pathlib
import uuid
import re
import tempfile
import zipfile

SCHEMA = "theseus.session-search.xai-export-child.v1"
ADAPTER = "xai-export"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_SAFE_ID = re.compile(r"[^A-Za-z0-9._~-]+")
_BACKEND_MEMBER = "prod-grok-backend.json"


@dataclasses.dataclass(frozen=True)
class _MaterializedSnapshot:
    artifacts: tuple[pathlib.Path, ...]
    source_export_sha256: str
    conversation_count: int


def _stable_json_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_mongo_time(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"$date"}:
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: invalid create_time")
    date = value["$date"]
    if not isinstance(date, dict) or set(date) != {"$numberLong"}:
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: invalid create_time")
    raw = date["$numberLong"]
    if not isinstance(raw, str):
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: invalid create_time")
    try:
        millis = int(raw)
    except ValueError as exc:
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: invalid create_time") from exc
    return millis / 1000.0


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


def _backend_payload(raw_zip: bytes) -> tuple[str, dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
            members = [name for name in zf.namelist() if pathlib.PurePosixPath(name.rstrip("/")).name == _BACKEND_MEMBER]
            if len(members) != 1:
                raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: expected exactly one prod-grok-backend.json")
            member = members[0]
            payload = json.loads(zf.read(member).decode("utf-8"))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: invalid official ZIP") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("conversations"), list):
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: conversations list missing")
    return member, payload


def _response_graph(conversation: dict, wrappers: object) -> tuple[dict[str, dict], list[str], str | None, dict[str, list[str]], list[str]]:
    conversation_id = conversation.get("id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: conversation id missing")
    if not isinstance(wrappers, list):
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: responses must be list")

    by_id: dict[str, dict] = {}
    source_order: list[str] = []
    for wrapper in wrappers:
        if not isinstance(wrapper, dict) or not isinstance(wrapper.get("response"), dict):
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: response wrapper malformed")
        response = wrapper["response"]
        rid = response.get("_id")
        if not isinstance(rid, str) or not rid:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: response id missing")
        if rid in by_id:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: duplicate response id")
        if response.get("conversation_id") != conversation_id:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: response conversation_id mismatch")
        if not isinstance(response.get("message"), str):
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: response message must be string")
        children_hint = response.get("children")
        if children_hint is not None and not isinstance(children_hint, list):
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: children hint must be list or null")
        by_id[rid] = response
        source_order.append(rid)

    if not by_id:
        return by_id, source_order, None, {}, []

    children: dict[str, list[str]] = {rid: [] for rid in by_id}
    roots: list[str] = []
    for rid, response in by_id.items():
        parent = response.get("parent_response_id")
        if isinstance(parent, str) and parent in by_id:
            children[parent].append(rid)
        elif parent is None or isinstance(parent, str):
            roots.append(rid)
        else:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: parent_response_id malformed")

    if len(roots) != 1:
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: expected one graph root")
    root_id = roots[0]

    for rid, response in by_id.items():
        hint = response.get("children")
        if hint is None:
            continue
        for entry in hint:
            if not isinstance(entry, dict) or not isinstance(entry.get("response_id"), str):
                raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: malformed children hint")
            child_id = entry["response_id"]
            child = by_id.get(child_id)
            if child is None or child.get("parent_response_id") != rid:
                raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: inconsistent children hint")

    seen: set[str] = set()
    stack = [root_id]
    while stack:
        rid = stack.pop()
        if rid in seen:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: response graph cycle")
        seen.add(rid)
        stack.extend(reversed(children[rid]))
    if seen != set(by_id):
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: disconnected response graph")

    leaves = sorted(rid for rid, child_ids in children.items() if not child_ids)
    return by_id, source_order, root_id, children, leaves


def _path_to_root(by_id: dict[str, dict], root_id: str, leaf_id: str) -> list[str]:
    if leaf_id not in by_id:
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: active leaf missing from responses")
    reverse: list[str] = []
    seen: set[str] = set()
    current: str | None = leaf_id
    while current is not None:
        if current in seen:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: response graph cycle")
        seen.add(current)
        response = by_id.get(current)
        if response is None:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: broken response path")
        reverse.append(current)
        if current == root_id:
            break
        parent = response.get("parent_response_id")
        if not isinstance(parent, str) or parent not in by_id:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: leaf is not rooted")
        current = parent
    path = list(reversed(reverse))
    if not path or path[0] != root_id:
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: leaf is not rooted")
    return path


def _mapped_role(sender: object, on_selected_path: bool) -> str:
    if not on_selected_path:
        return "unknown"
    if sender == "human":
        return "user"
    if sender in {"assistant", "ASSISTANT"}:
        return "assistant"
    return "unknown"


def _response_message(response: dict, on_selected_path: bool, order: int) -> dict:
    rid = response["_id"]
    return {
        "id": rid,
        "author": {"role": _mapped_role(response.get("sender"), on_selected_path)},
        "create_time": _parse_mongo_time(response.get("create_time")),
        "content": {"content_type": "text", "parts": [response["message"]]},
        "metadata": {
            "xai_response_id": rid,
            "xai_parent_response_id": response.get("parent_response_id"),
            "xai_sender": response.get("sender"),
            "xai_model": response.get("model"),
            "xai_selected_path": bool(on_selected_path),
            "session_search_order": order,
        },
    }


def _conversation_variants(item: dict) -> list[dict]:
    conversation = item.get("conversation")
    if not isinstance(conversation, dict):
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: conversation object missing")
    source_session_id = conversation.get("id")
    title = conversation.get("title")
    if not isinstance(source_session_id, str) or not source_session_id or not isinstance(title, str):
        raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: conversation identity/title missing")

    by_id, source_order, root_id, children, leaves = _response_graph(conversation, item.get("responses"))
    explicit_leaf = conversation.get("leaf_response_id")
    if not by_id:
        if explicit_leaf is not None:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: active leaf declared for empty response graph")
        return [{
            "conversation_id": source_session_id,
            "title": title,
            "messages": [],
            "page_info": {"has_previous_page": False, "has_next_page": False},
            "xai_source_conversation_id": source_session_id,
            "xai_branch_mode": "empty",
            "xai_selected_leaf_id": None,
            "xai_leaf_count": 0,
        }]

    selections: list[tuple[str, str]] = []
    if explicit_leaf is not None:
        if not isinstance(explicit_leaf, str) or explicit_leaf not in by_id:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: active leaf invalid")
        if children[explicit_leaf]:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: active leaf is not terminal")
        selections = [(explicit_leaf, "explicit-active")]
    elif len(leaves) == 1:
        selections = [(leaves[0], "unique-leaf")]
    else:
        selections = [(leaf, "branch-variant") for leaf in leaves]

    variants: list[dict] = []
    for leaf_id, mode in selections:
        path_ids = _path_to_root(by_id, root_id, leaf_id)
        selected = set(path_ids)
        branch_choices = []
        for parent_id, child_id in zip(path_ids, path_ids[1:]):
            siblings = children[parent_id]
            if len(siblings) > 1 and child_id != siblings[0]:
                branch_choices.append([parent_id, child_id])
        session_id = source_session_id
        if branch_choices:
            branch_hash = hashlib.sha256(_stable_json_bytes(branch_choices)).hexdigest()[:12]
            session_id = f"{source_session_id}~branch-{branch_hash}"
        ordered_ids = path_ids + [rid for rid in source_order if rid not in selected]
        messages = [_response_message(by_id[rid], rid in selected, order) for order, rid in enumerate(ordered_ids)]
        variants.append({
            "conversation_id": session_id,
            "title": title,
            "messages": messages,
            "page_info": {"has_previous_page": False, "has_next_page": False},
            "xai_source_conversation_id": source_session_id,
            "xai_branch_mode": mode,
            "xai_selected_leaf_id": leaf_id,
            "xai_leaf_count": len(leaves),
        })
    return variants


def _publish_content_addressed(target: pathlib.Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: content-address collision")
        return
    temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: concurrent content-address collision")
            return
        os.replace(temp, target)
        if target.read_bytes() != data:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: published artifact mismatch")
    finally:
        if temp.exists():
            temp.unlink()


def _materialize_export_snapshot(source: pathlib.Path, output_dir: pathlib.Path) -> _MaterializedSnapshot:
    source = pathlib.Path(source)
    output_dir = pathlib.Path(output_dir)
    raw_zip = source.read_bytes()
    parent_sha = _sha256(raw_zip)
    backend_member, backend = _backend_payload(raw_zip)
    conversations = backend["conversations"]
    artifacts: list[pathlib.Path] = []
    pending: list[tuple[pathlib.Path, bytes]] = []
    seen_source_ids: set[str] = set()
    seen_session_ids: set[str] = set()
    for item in conversations:
        if not isinstance(item, dict):
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: conversation entry must be object")
        conversation = item.get("conversation")
        source_id = conversation.get("id") if isinstance(conversation, dict) else None
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: conversation id missing")
        if source_id in seen_source_ids:
            raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: duplicate conversation id")
        seen_source_ids.add(source_id)
        for payload in _conversation_variants(item):
            session_id = payload["conversation_id"]
            if session_id in seen_session_ids:
                raise ValueError("BLOCKED_UNSUPPORTED_XAI_EXPORT: duplicate materialized session id")
            seen_session_ids.add(session_id)
            sanitized = _SAFE_ID.sub("_", session_id).strip("._") or "session"
            id_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
            safe_id = f"{sanitized}-{id_hash}"
            member = f"optional/conversation-xai-{safe_id}.bin"
            payload_bytes = _stable_json_bytes(payload)
            manifest = {
                "schema": SCHEMA,
                "source_adapter": ADAPTER,
                "source_export_sha256": parent_sha,
                "source_export_bytes": len(raw_zip),
                "source_backend_member": backend_member,
                "source_conversation_id": source_id,
                "session_id": session_id,
                "branch_mode": payload["xai_branch_mode"],
                "selected_leaf_id": payload["xai_selected_leaf_id"],
                "files": [{"name": member, "bytes": len(payload_bytes), "sha256": _sha256(payload_bytes)}],
            }
            archive_bytes = _portable_zip_bytes(member, payload_bytes, manifest)
            archive_sha = _sha256(archive_bytes)
            target = output_dir / f"xai-{safe_id}-{archive_sha[:16]}.zip"
            pending.append((target, archive_bytes))
            artifacts.append(target)

    output_dir.mkdir(parents=True, exist_ok=True)
    for target, archive_bytes in pending:
        _publish_content_addressed(target, archive_bytes)
    return _MaterializedSnapshot(tuple(artifacts), parent_sha, len(conversations))


def materialize_export(source: pathlib.Path, output_dir: pathlib.Path) -> list[pathlib.Path]:
    return list(_materialize_export_snapshot(source, output_dir).artifacts)


def ingest_export(source: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    from .corpus_store import ingest_many

    with tempfile.TemporaryDirectory(prefix="session-search-xai-") as td:
        snapshot = _materialize_export_snapshot(pathlib.Path(source), pathlib.Path(td))
        child_ids = {str(path): f"xai-child-sha256:{_sha256(path.read_bytes())}" for path in snapshot.artifacts}
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
    p = argparse.ArgumentParser(description="Materialize an official xAI/Grok data export ZIP into portable Session Search artifacts.")
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
        print(f"XAI EXPORT FAILED: {exc}")
        return 1
    print(json.dumps({"artifacts": [str(path) for path in outputs], "count": len(outputs)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
