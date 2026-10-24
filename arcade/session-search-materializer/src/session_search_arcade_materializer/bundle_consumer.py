from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zipfile import ZIP_STORED, BadZipFile, ZipFile

BUNDLE_SCHEMA = "theseus.session-search-shard-bundle.v1"
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
BUNDLE_MEMBERS = ("manifest.json", "sessions.csv", "messages.csv")
DEFAULT_MAX_BUNDLE_BYTES = 512 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")


class BundleVerificationError(ValueError):
    pass


def _require_sha256(value: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise BundleVerificationError("EXPECTED_BUNDLE_SHA256_INVALID")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _member_digest(zf: ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with zf.open(name, "r") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(zf: ZipFile, name: str, columns: tuple[str, ...]) -> list[tuple[str, ...]]:
    with zf.open(name, "r") as raw:
        wrapper = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        reader = csv.reader(wrapper)
        try:
            header = tuple(next(reader))
        except StopIteration as exc:
            raise BundleVerificationError("BUNDLE_CSV_EMPTY") from exc
        if header != columns:
            raise BundleVerificationError("BUNDLE_CSV_COLUMNS_INVALID")
        rows: list[tuple[str, ...]] = []
        for row in reader:
            if len(row) != len(columns):
                raise BundleVerificationError("BUNDLE_ROW_SHAPE_INVALID")
            if any("\x00" in value for value in row):
                raise BundleVerificationError("BUNDLE_TEXT_NUL")
            rows.append(tuple(row))
        return rows


def _typed_sessions(rows: list[tuple[str, ...]]) -> list[tuple[Any, ...]]:
    typed = []
    try:
        for session_id, title, coverage_state, time_min, time_max in rows:
            typed.append((
                session_id,
                title,
                coverage_state,
                None if time_min == "" else float(time_min),
                None if time_max == "" else float(time_max),
            ))
    except ValueError as exc:
        raise BundleVerificationError("BUNDLE_TYPED_ROW_INVALID") from exc
    return typed


def _typed_messages(rows: list[tuple[str, ...]]) -> list[tuple[Any, ...]]:
    typed = []
    try:
        for session_id, ordinal, message_id, role, search_class, create_time, text in rows:
            typed.append((
                session_id,
                int(ordinal),
                message_id,
                role,
                search_class,
                None if create_time == "" else float(create_time),
                text,
            ))
    except ValueError as exc:
        raise BundleVerificationError("BUNDLE_TYPED_ROW_INVALID") from exc
    return typed


def _projection_digest(sessions: list[tuple[Any, ...]], messages: list[tuple[Any, ...]]) -> str:
    digest = hashlib.sha256()
    for kind, rows in (("session", sessions), ("message", messages)):
        for row in rows:
            payload = json.dumps(
                [kind, *row], ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            digest.update(payload)
            digest.update(b"\n")
    return digest.hexdigest()


def _source_manifest_digest(manifest: dict[str, Any]) -> str:
    fields = (
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
        core = {field: manifest[field] for field in fields}
    except KeyError as exc:
        raise BundleVerificationError("BUNDLE_SOURCE_MANIFEST_INCOMPLETE") from exc
    raw = json.dumps(
        core, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_bundle(
    path: Path,
    *,
    expected_bundle_sha256: str,
    max_bundle_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
) -> dict[str, Any]:
    expected = _require_sha256(expected_bundle_sha256)
    path = Path(path)
    if not path.is_file():
        raise BundleVerificationError("BUNDLE_FILE_MISSING")
    size = path.stat().st_size
    if size < 1 or size > max_bundle_bytes:
        raise BundleVerificationError("BUNDLE_SIZE_OUT_OF_BOUNDS")
    actual = _sha256_file(path)
    if actual != expected:
        raise BundleVerificationError("BUNDLE_SHA256_MISMATCH")

    try:
        with ZipFile(path, "r") as zf:
            if sorted(zf.namelist()) != sorted(BUNDLE_MEMBERS):
                raise BundleVerificationError("BUNDLE_MEMBER_SET_INVALID")
            for name in BUNDLE_MEMBERS:
                info = zf.getinfo(name)
                if info.compress_type != ZIP_STORED:
                    raise BundleVerificationError("BUNDLE_COMPRESSION_UNSUPPORTED")
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BundleVerificationError("BUNDLE_MANIFEST_INVALID") from exc
            if not isinstance(manifest, dict) or manifest.get("schema") != BUNDLE_SCHEMA:
                raise BundleVerificationError("BUNDLE_SCHEMA_UNSUPPORTED")
            files = manifest.get("files")
            if not isinstance(files, dict) or set(files) != {"sessions.csv", "messages.csv"}:
                raise BundleVerificationError("BUNDLE_FILE_MANIFEST_INVALID")

            for name, columns in (("sessions.csv", SESSION_COLUMNS), ("messages.csv", MESSAGE_COLUMNS)):
                metadata = files.get(name)
                if not isinstance(metadata, dict):
                    raise BundleVerificationError("BUNDLE_FILE_MANIFEST_INVALID")
                if metadata.get("columns") != list(columns):
                    raise BundleVerificationError("BUNDLE_FILE_COLUMNS_MISMATCH")
                if metadata.get("sha256") != _member_digest(zf, name):
                    raise BundleVerificationError("BUNDLE_FILE_DIGEST_MISMATCH")
                if metadata.get("size_bytes") != zf.getinfo(name).file_size:
                    raise BundleVerificationError("BUNDLE_FILE_SIZE_MISMATCH")

            session_raw = _read_csv(zf, "sessions.csv", SESSION_COLUMNS)
            message_raw = _read_csv(zf, "messages.csv", MESSAGE_COLUMNS)
            if files["sessions.csv"].get("rows") != len(session_raw):
                raise BundleVerificationError("BUNDLE_ROW_COUNT_MISMATCH")
            if files["messages.csv"].get("rows") != len(message_raw):
                raise BundleVerificationError("BUNDLE_ROW_COUNT_MISMATCH")
            if manifest.get("session_count") != len(session_raw):
                raise BundleVerificationError("BUNDLE_SESSION_COUNT_MISMATCH")
            if manifest.get("message_count") != len(message_raw):
                raise BundleVerificationError("BUNDLE_MESSAGE_COUNT_MISMATCH")

            sessions = _typed_sessions(session_raw)
            messages = _typed_messages(message_raw)
            projection_sha = _projection_digest(sessions, messages)
            if manifest.get("source_projection_sha256") != projection_sha:
                raise BundleVerificationError("BUNDLE_SOURCE_PROJECTION_MISMATCH")
            text_bytes = sum(len(row[6].encode("utf-8")) for row in messages)
            if manifest.get("searchable_text_bytes") != text_bytes:
                raise BundleVerificationError("BUNDLE_TEXT_BYTES_MISMATCH")
            source_manifest_sha = _source_manifest_digest(manifest)
            if manifest.get("source_manifest_sha256") != source_manifest_sha:
                raise BundleVerificationError("BUNDLE_SOURCE_MANIFEST_MISMATCH")
            generation_id = hashlib.sha256(
                ("session-search-generation-v1\n" + source_manifest_sha).encode("ascii")
            ).hexdigest()
            if manifest.get("generation_id") != generation_id:
                raise BundleVerificationError("BUNDLE_GENERATION_ID_MISMATCH")
    except BadZipFile as exc:
        raise BundleVerificationError("BUNDLE_ZIP_INVALID") from exc

    return {
        "status": "VERIFIED",
        "schema": BUNDLE_SCHEMA,
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
        "bundle_sha256": actual,
        "bundle_size_bytes": size,
    }


async def download_drive_bundle(
    *,
    drive_file_id: str,
    oauth_token: str,
    expected_bundle_sha256: str,
    destination: Path,
    client: Any | None = None,
    max_bundle_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
) -> dict[str, Any]:
    expected = _require_sha256(expected_bundle_sha256)
    if not isinstance(drive_file_id, str) or not _DRIVE_FILE_ID_RE.fullmatch(drive_file_id):
        raise BundleVerificationError("DRIVE_FILE_ID_INVALID")
    if not oauth_token:
        raise BundleVerificationError("GOOGLE_OAUTH_TOKEN_MISSING")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    if own_client:
        import httpx
        client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0))

    url = f"https://www.googleapis.com/drive/v3/files/{quote(drive_file_id, safe='')}"
    headers = {"Authorization": f"Bearer {oauth_token}"}
    params = {"alt": "media", "supportsAllDrives": "true"}
    digest = hashlib.sha256()
    total = 0
    try:
        async with client.stream("GET", url, headers=headers, params=params) as response:
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length is not None and int(content_length) > max_bundle_bytes:
                raise BundleVerificationError("BUNDLE_SIZE_OUT_OF_BOUNDS")
            with destination.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bundle_bytes:
                        raise BundleVerificationError("BUNDLE_SIZE_OUT_OF_BOUNDS")
                    digest.update(chunk)
                    handle.write(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            raise BundleVerificationError("BUNDLE_SHA256_MISMATCH")
        return {"sha256": actual, "size_bytes": total}
    except Exception:
        if destination.exists():
            destination.unlink()
        raise
    finally:
        if own_client:
            await client.aclose()
