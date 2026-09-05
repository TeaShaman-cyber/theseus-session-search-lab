from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import tempfile
import uuid
import zipfile
from datetime import datetime

SCHEMA = "theseus.session-search.speed-booster-export-child.v1"
ADAPTER = "speed-booster-export"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _stable_json_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_time(value: object) -> float | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: invalid timestamp type")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: timestamp must be timezone-aware")
    return parsed.timestamp()


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
            raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: content-address collision")
        return
    temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: concurrent content-address collision")
            return
        os.replace(temp, target)
        if target.read_bytes() != data:
            raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: published artifact mismatch")
    finally:
        if temp.exists():
            temp.unlink()


def _load_export(source: pathlib.Path) -> tuple[bytes, dict]:
    raw = pathlib.Path(source).read_bytes()
    try:
        obj = json.loads(raw)
    except Exception as exc:
        raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: invalid JSON") from exc
    if not isinstance(obj, dict):
        raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: top level must be object")
    title = obj.get("title")
    messages = obj.get("messages")
    exported_at = obj.get("exported_at")
    created_at = obj.get("created_at")
    if not isinstance(title, str) or not title or not isinstance(messages, list) or not messages:
        raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: title/messages missing")
    if not isinstance(exported_at, str) or not exported_at or not isinstance(created_at, str) or not created_at:
        raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: export timestamps missing")
    _parse_time(exported_at)
    _parse_time(created_at)
    return raw, obj


def _session_id(obj: dict) -> str:
    title = obj["title"]
    first = obj["messages"][0]
    if not isinstance(first, dict):
        raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: first message must be object")
    first_time = _parse_time(first.get("create_time"))
    if first_time is None:
        raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: first message timestamp missing")
    digest = _sha256(_stable_json_bytes([title, first_time, first.get("role"), first.get("content")]))[:24]
    return f"speed-booster-v1:{digest}"


def _message(raw: dict, order: int) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: message must be object")
    role = raw.get("role")
    content = raw.get("content")
    if not isinstance(content, str):
        raise ValueError("BLOCKED_UNSUPPORTED_SPEED_BOOSTER_EXPORT: message content must be string")
    create_time = _parse_time(raw.get("create_time"))
    mapped_role = role if role in {"user", "assistant"} else "unknown"
    message_digest = _sha256(_stable_json_bytes([order, role, create_time, content]))[:32]
    return {
        "id": f"speed-booster-message-v1:{message_digest}",
        "author": {"role": mapped_role},
        "create_time": create_time,
        "content": {"content_type": "text", "parts": [content]},
        "metadata": {
            "speed_booster_role": role,
            "speed_booster_create_time_iso": raw.get("create_time"),
            "speed_booster_model": raw.get("model"),
            "speed_booster_sources": raw.get("sources"),
            "speed_booster_images": raw.get("images"),
            "session_search_order": order,
        },
    }


def _materialize_export_snapshot(source: pathlib.Path, output_dir: pathlib.Path) -> tuple[list[pathlib.Path], str]:
    raw, obj = _load_export(pathlib.Path(source))
    parent_sha = _sha256(raw)
    session_id = _session_id(obj)
    messages = [_message(item, order) for order, item in enumerate(obj["messages"])]
    payload = {
        "conversation_id": session_id,
        "title": obj["title"],
        "messages": messages,
        "page_info": {"has_previous_page": True, "has_next_page": False},
        "speed_booster_exported_at": obj.get("exported_at"),
        "speed_booster_created_at": obj.get("created_at"),
    }
    safe = _SAFE_ID.sub("_", session_id).strip("._") or "session"
    member = f"optional/conversation-speed-booster-{safe}.bin"
    payload_bytes = _stable_json_bytes(payload)
    manifest = {
        "schema": SCHEMA,
        "source_adapter": ADAPTER,
        "source_export_sha256": parent_sha,
        "source_export_bytes": len(raw),
        "session_id": session_id,
        "files": [{"name": member, "bytes": len(payload_bytes), "sha256": _sha256(payload_bytes)}],
    }
    archive = _portable_zip_bytes(member, payload_bytes, manifest)
    target = pathlib.Path(output_dir) / f"speed-booster-{safe}-{_sha256(archive)[:16]}.zip"
    _publish_content_addressed(target, archive)
    return [target], parent_sha


def materialize_export(source: pathlib.Path, output_dir: pathlib.Path) -> list[pathlib.Path]:
    artifacts, _source_sha = _materialize_export_snapshot(source, output_dir)
    return artifacts


def ingest_export(source: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    from .corpus_store import ingest_many

    source = pathlib.Path(source)
    with tempfile.TemporaryDirectory(prefix="session-search-speed-booster-") as td:
        artifacts, source_sha = _materialize_export_snapshot(source, pathlib.Path(td))
        child_ids = {str(path): f"speed-booster-child-sha256:{_sha256(path.read_bytes())}" for path in artifacts}
        result = ingest_many(artifacts, pathlib.Path(corpus_root))
        for item in result.get("results", []):
            source_value = item.get("source")
            if source_value in child_ids:
                item["source"] = child_ids[source_value]
    return {
        **result,
        "conversation_count": 1,
        "artifact_count": len(artifacts),
        "source_export_sha256": source_sha,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize a Speed Booster Toolkit ChatGPT JSON export into a portable Session Search artifact.")
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
        print(f"SPEED BOOSTER EXPORT FAILED: {exc}")
        return 1
    print(json.dumps({"artifacts": [str(path) for path in outputs], "count": len(outputs)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
