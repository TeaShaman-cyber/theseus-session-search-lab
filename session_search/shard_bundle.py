from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from .neon_materializer import (
    _capture_source_snapshot,
    _projection_source_digest,
)


BUNDLE_SCHEMA = "theseus.session-search-shard-bundle.v1"
BUNDLE_CSV_FIELD_LIMIT = 8 * 1024 * 1024
SESSION_COLUMNS = ("session_id", "title", "coverage_state", "time_min", "time_max")
MESSAGE_COLUMNS = (
    "session_id",
    "ordinal",
    "message_id",
    "role",
    "search_class",
    "create_time",
    "text",
)
_BUNDLE_MEMBERS = ("manifest.json", "sessions.csv", "messages.csv")
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[tuple[Any, ...]]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            if len(row) != len(columns):
                raise ValueError("BUNDLE_ROW_SHAPE_INVALID")
            if any(
                isinstance(value, str) and len(value) > BUNDLE_CSV_FIELD_LIMIT
                for value in row
            ):
                raise ValueError("BUNDLE_CSV_FIELD_TOO_LARGE")
            if any(isinstance(value, str) and "\x00" in value for value in row):
                raise ValueError("BUNDLE_TEXT_NUL")
            writer.writerow(row)


def _file_meta(path: Path, rows: int, columns: tuple[str, ...]) -> dict[str, Any]:
    return {
        "sha256": _sha256_file(path),
        "size_bytes": Path(path).stat().st_size,
        "rows": rows,
        "columns": list(columns),
    }


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(filename=name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o600 << 16
    return info


def _public_summary(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": manifest["schema"],
        "provider": manifest["provider"],
        "shard_id": manifest["shard_id"],
        "generation_id": manifest["generation_id"],
        "source_manifest_sha256": manifest["source_manifest_sha256"],
        "source_projection_sha256": manifest["source_projection_sha256"],
        "accepted_artifact_count": manifest["accepted_artifact_count"],
        "accepted_set_sha256": manifest["accepted_set_sha256"],
        "session_count": manifest["session_count"],
        "message_count": manifest["message_count"],
        "searchable_text_bytes": manifest["searchable_text_bytes"],
        "bundle_sha256": _sha256_file(path),
        "bundle_size_bytes": path.stat().st_size,
        "files": {
            name: {
                "sha256": manifest["files"][name]["sha256"],
                "size_bytes": manifest["files"][name]["size_bytes"],
                "rows": manifest["files"][name]["rows"],
            }
            for name in ("sessions.csv", "messages.csv")
        },
    }


def build_bundle(
    corpus_root: Path,
    *,
    provider: str,
    shard_id: str,
    output_path: Path,
    require_verified: bool = True,
) -> dict[str, Any]:
    corpus_root = Path(corpus_root)
    output_path = Path(output_path)
    source_manifest, sessions, messages = _capture_source_snapshot(
        corpus_root,
        provider=provider,
        shard_id=shard_id,
        require_verified=require_verified,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="session-search-bundle-") as temp_dir:
        temp = Path(temp_dir)
        sessions_path = temp / "sessions.csv"
        messages_path = temp / "messages.csv"
        _write_csv(sessions_path, SESSION_COLUMNS, sessions)
        _write_csv(messages_path, MESSAGE_COLUMNS, messages)

        manifest = {
            "schema": BUNDLE_SCHEMA,
            **source_manifest,
            "files": {
                "sessions.csv": _file_meta(
                    sessions_path, len(sessions), SESSION_COLUMNS
                ),
                "messages.csv": _file_meta(
                    messages_path, len(messages), MESSAGE_COLUMNS
                ),
            },
        }
        manifest_bytes = _canonical_json_bytes(manifest)

        tmp_output = output_path.with_name(output_path.name + ".tmp")
        if tmp_output.exists():
            tmp_output.unlink()
        try:
            with ZipFile(tmp_output, "w", compression=ZIP_STORED, allowZip64=True) as zf:
                zf.writestr(_zip_info("manifest.json"), manifest_bytes)
                for name, source in (
                    ("sessions.csv", sessions_path),
                    ("messages.csv", messages_path),
                ):
                    with source.open("rb") as handle:
                        zf.writestr(_zip_info(name), handle.read())
            os.replace(tmp_output, output_path)
        finally:
            if tmp_output.exists():
                tmp_output.unlink()

    return _public_summary(output_path, manifest)


def _read_manifest(zf: ZipFile) -> dict[str, Any]:
    try:
        payload = json.loads(zf.read("manifest.json").decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("BUNDLE_MANIFEST_INVALID") from exc
    if payload.get("schema") != BUNDLE_SCHEMA:
        raise ValueError("BUNDLE_SCHEMA_UNSUPPORTED")
    return payload


def _member_digest(zf: ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with zf.open(name, "r") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _csv_rows(zf: ZipFile, name: str, columns: tuple[str, ...]) -> list[tuple[Any, ...]]:
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(BUNDLE_CSV_FIELD_LIMIT)
    try:
        with zf.open(name, "r") as raw:
            import io

            wrapper = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.reader(wrapper)
            try:
                header = tuple(next(reader))
            except StopIteration as exc:
                raise ValueError("BUNDLE_CSV_EMPTY") from exc
            if header != columns:
                raise ValueError("BUNDLE_CSV_COLUMNS_INVALID")
            rows: list[tuple[Any, ...]] = []
            for row in reader:
                if len(row) != len(columns):
                    raise ValueError("BUNDLE_ROW_SHAPE_INVALID")
                rows.append(tuple(row))
            return rows
    finally:
        csv.field_size_limit(previous_limit)


def _typed_sessions(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    typed = []
    for session_id, title, coverage_state, time_min, time_max in rows:
        typed.append((
            session_id, title, coverage_state,
            None if time_min == "" else float(time_min),
            None if time_max == "" else float(time_max),
        ))
    return typed


def _typed_messages(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    typed = []
    for session_id, ordinal, message_id, role, search_class, create_time, text in rows:
        typed.append((
            session_id, int(ordinal), message_id, role, search_class,
            None if create_time == "" else float(create_time), text,
        ))
    return typed


def _validate_source_identity(
    manifest: dict[str, Any],
    sessions: list[tuple[Any, ...]],
    messages: list[tuple[Any, ...]],
) -> None:
    projection_digest = _projection_source_digest(sessions, messages)
    if manifest.get("source_projection_sha256") != projection_digest:
        raise ValueError("BUNDLE_SOURCE_PROJECTION_MISMATCH")
    text_bytes = sum(len(row[6].encode("utf-8")) for row in messages)
    if manifest.get("searchable_text_bytes") != text_bytes:
        raise ValueError("BUNDLE_TEXT_BYTES_MISMATCH")

    source_fields = (
        "materializer_contract_version",
        "projection_schema_version",
        "provider",
        "shard_id",
        "searchable_classes",
        "accepted_artifact_count",
        "accepted_set_sha256",
        "source_projection_sha256",
        "source_adapters",
        "session_count",
        "message_count",
        "searchable_text_bytes",
    )
    try:
        source_core = {key: manifest[key] for key in source_fields}
    except KeyError as exc:
        raise ValueError("BUNDLE_SOURCE_MANIFEST_INCOMPLETE") from exc
    source_manifest_sha256 = hashlib.sha256(
        json.dumps(
            source_core, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    if manifest.get("source_manifest_sha256") != source_manifest_sha256:
        raise ValueError("BUNDLE_SOURCE_MANIFEST_MISMATCH")
    generation_id = hashlib.sha256(
        ("session-search-generation-v1\n" + source_manifest_sha256).encode("ascii")
    ).hexdigest()
    if manifest.get("generation_id") != generation_id:
        raise ValueError("BUNDLE_GENERATION_ID_MISMATCH")


def verify_bundle(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ValueError("BUNDLE_FILE_MISSING")

    with ZipFile(path, "r") as zf:
        if sorted(zf.namelist()) != sorted(_BUNDLE_MEMBERS):
            raise ValueError("BUNDLE_MEMBER_SET_INVALID")
        manifest = _read_manifest(zf)
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != {"sessions.csv", "messages.csv"}:
            raise ValueError("BUNDLE_FILE_MANIFEST_INVALID")

        for name in ("sessions.csv", "messages.csv"):
            metadata = files.get(name)
            if not isinstance(metadata, dict):
                raise ValueError("BUNDLE_FILE_MANIFEST_INVALID")
            if metadata.get("sha256") != _member_digest(zf, name):
                raise ValueError("BUNDLE_FILE_DIGEST_MISMATCH")
            try:
                member_size = zf.getinfo(name).file_size
            except KeyError as exc:
                raise ValueError("BUNDLE_MEMBER_SET_INVALID") from exc
            if metadata.get("size_bytes") != member_size:
                raise ValueError("BUNDLE_FILE_SIZE_MISMATCH")

        session_raw = _csv_rows(zf, "sessions.csv", SESSION_COLUMNS)
        message_raw = _csv_rows(zf, "messages.csv", MESSAGE_COLUMNS)
        session_rows = len(session_raw)
        message_rows = len(message_raw)
        if files["sessions.csv"].get("rows") != session_rows:
            raise ValueError("BUNDLE_ROW_COUNT_MISMATCH")
        if files["messages.csv"].get("rows") != message_rows:
            raise ValueError("BUNDLE_ROW_COUNT_MISMATCH")
        if manifest.get("session_count") != session_rows:
            raise ValueError("BUNDLE_SESSION_COUNT_MISMATCH")
        if manifest.get("message_count") != message_rows:
            raise ValueError("BUNDLE_MESSAGE_COUNT_MISMATCH")
        _validate_source_identity(
            manifest, _typed_sessions(session_raw), _typed_messages(message_raw)
        )

    return _public_summary(path, manifest)
