from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import threading
from pathlib import Path
from collections.abc import Iterator
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
_CSV_LIMIT_LOCK = threading.RLock()


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


def _iter_csv(
    zf: ZipFile,
    name: str,
    columns: tuple[str, ...],
    *,
    field_limit: int,
) -> Iterator[tuple[str, ...]]:
    # csv.field_size_limit is process-global. Serialize the temporary override so
    # concurrent Hosted tool calls cannot restore another parser's limit.
    with _CSV_LIMIT_LOCK:
        prior_limit = csv.field_size_limit()
        if prior_limit < field_limit:
            csv.field_size_limit(field_limit)
        try:
            with zf.open(name, "r") as raw:
                wrapper = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                reader = csv.reader(wrapper)
                try:
                    header = tuple(next(reader))
                except StopIteration as exc:
                    raise BundleVerificationError("BUNDLE_CSV_EMPTY") from exc
                if header != columns:
                    raise BundleVerificationError("BUNDLE_CSV_COLUMNS_INVALID")
                for row in reader:
                    if len(row) != len(columns):
                        raise BundleVerificationError("BUNDLE_ROW_SHAPE_INVALID")
                    if any("\x00" in value for value in row):
                        raise BundleVerificationError("BUNDLE_TEXT_NUL")
                    yield tuple(row)
        finally:
            if csv.field_size_limit() != prior_limit:
                csv.field_size_limit(prior_limit)


def _read_csv(
    zf: ZipFile,
    name: str,
    columns: tuple[str, ...],
    *,
    field_limit: int,
) -> list[tuple[str, ...]]:
    return list(_iter_csv(zf, name, columns, field_limit=field_limit))


def _typed_session(row: tuple[str, ...]) -> tuple[Any, ...]:
    try:
        session_id, title, coverage_state, time_min, time_max = row
        return (
            session_id,
            title,
            coverage_state,
            None if time_min == "" else float(time_min),
            None if time_max == "" else float(time_max),
        )
    except (ValueError, TypeError) as exc:
        raise BundleVerificationError("BUNDLE_TYPED_ROW_INVALID") from exc


def _typed_message(row: tuple[str, ...]) -> tuple[Any, ...]:
    try:
        session_id, ordinal, message_id, role, search_class, create_time, text = row
        return (
            session_id,
            int(ordinal),
            message_id,
            role,
            search_class,
            None if create_time == "" else float(create_time),
            text,
        )
    except (ValueError, TypeError) as exc:
        raise BundleVerificationError("BUNDLE_TYPED_ROW_INVALID") from exc

def _typed_sessions(rows: list[tuple[str, ...]]) -> list[tuple[Any, ...]]:
    return [_typed_session(row) for row in rows]


def _typed_messages(rows: list[tuple[str, ...]]) -> list[tuple[Any, ...]]:
    return [_typed_message(row) for row in rows]


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



def _update_projection_digest(
    digest: "hashlib._Hash", kind: str, row: tuple[Any, ...]
) -> None:
    payload = json.dumps(
        [kind, *row], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    digest.update(payload)
    digest.update(b"\n")

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


def _validate_bundle_structure(
    zf: ZipFile,
) -> tuple[dict[str, Any], dict[str, Any]]:
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
    return manifest, files


def _result_from_manifest(
    manifest: dict[str, Any], *, actual_sha256: str, bundle_size_bytes: int
) -> dict[str, Any]:
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
        "bundle_sha256": actual_sha256,
        "bundle_size_bytes": bundle_size_bytes,
        "projection_schema_version": manifest["projection_schema_version"],
    }


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
            manifest, files = _validate_bundle_structure(zf)
            projection = hashlib.sha256()
            session_count = 0
            message_count = 0
            text_bytes = 0
            for raw in _iter_csv(
                zf, "sessions.csv", SESSION_COLUMNS, field_limit=max_bundle_bytes
            ):
                row = _typed_session(raw)
                _update_projection_digest(projection, "session", row)
                session_count += 1
            for raw in _iter_csv(
                zf, "messages.csv", MESSAGE_COLUMNS, field_limit=max_bundle_bytes
            ):
                row = _typed_message(raw)
                _update_projection_digest(projection, "message", row)
                text_bytes += len(row[6].encode("utf-8"))
                message_count += 1

            if files["sessions.csv"].get("rows") != session_count:
                raise BundleVerificationError("BUNDLE_ROW_COUNT_MISMATCH")
            if files["messages.csv"].get("rows") != message_count:
                raise BundleVerificationError("BUNDLE_ROW_COUNT_MISMATCH")
            if manifest.get("session_count") != session_count:
                raise BundleVerificationError("BUNDLE_SESSION_COUNT_MISMATCH")
            if manifest.get("message_count") != message_count:
                raise BundleVerificationError("BUNDLE_MESSAGE_COUNT_MISMATCH")
            if manifest.get("source_projection_sha256") != projection.hexdigest():
                raise BundleVerificationError("BUNDLE_SOURCE_PROJECTION_MISMATCH")
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

    return _result_from_manifest(
        manifest, actual_sha256=actual, bundle_size_bytes=size
    )


def iter_bundle_sessions(
    path: Path, *, field_limit: int = DEFAULT_MAX_BUNDLE_BYTES
) -> Iterator[tuple[Any, ...]]:
    with ZipFile(Path(path), "r") as zf:
        for raw in _iter_csv(zf, "sessions.csv", SESSION_COLUMNS, field_limit=field_limit):
            yield _typed_session(raw)


def iter_bundle_messages(
    path: Path, *, field_limit: int = DEFAULT_MAX_BUNDLE_BYTES
) -> Iterator[tuple[Any, ...]]:
    with ZipFile(Path(path), "r") as zf:
        for raw in _iter_csv(zf, "messages.csv", MESSAGE_COLUMNS, field_limit=field_limit):
            yield _typed_message(raw)


def read_verified_bundle(
    path: Path,
    *,
    expected_bundle_sha256: str,
    max_bundle_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
) -> tuple[dict[str, Any], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Compatibility helper for callers that explicitly need all rows in memory."""
    result = verify_bundle(
        path,
        expected_bundle_sha256=expected_bundle_sha256,
        max_bundle_bytes=max_bundle_bytes,
    )
    sessions = list(iter_bundle_sessions(path, field_limit=max_bundle_bytes))
    messages = list(iter_bundle_messages(path, field_limit=max_bundle_bytes))
    return result, sessions, messages


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
