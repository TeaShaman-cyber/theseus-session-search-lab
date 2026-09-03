from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
import re
import zipfile

HIDDEN_TYPES = {"thoughts", "reasoning_recap", "model_editable_context"}
PAYLOAD_RE = re.compile(r"^optional/conversation(?:-messages)?-[^/]+\.bin$")
CANONICAL_MESSAGE_VERSION = "session-search-message-v1"


@dataclasses.dataclass(frozen=True)
class MessageSource:
    capture_sequence: int
    page_position: int
    member_name: str
    source_object_sha256: str


@dataclasses.dataclass(frozen=True)
class NormalizedMessage:
    message_id: str | None
    role: str
    content_type: str
    search_class: str
    create_time: float | None
    text: str
    canonical_message_sha256: str
    sources: tuple[MessageSource, ...]


@dataclasses.dataclass(frozen=True)
class NormalizedPage:
    capture_sequence: int
    member_name: str
    start_cursor: str | None
    end_cursor: str | None
    has_previous_page: bool
    has_next_page: bool
    message_count: int
    min_create_time: float | None
    max_create_time: float | None


@dataclasses.dataclass(frozen=True)
class NormalizedArtifact:
    source: pathlib.Path
    artifact_sha256: str
    size_bytes: int
    source_schema: str
    source_adapter: str
    session_id: str
    title: str
    coverage_state: str
    has_previous_page: bool
    has_next_page: bool
    pages: tuple[NormalizedPage, ...]
    messages: tuple[NormalizedMessage, ...]
    message_occurrences: int
    duplicate_message_occurrences: int


def _stable_json_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_json(obj: object) -> str:
    return hashlib.sha256(_stable_json_bytes(obj)).hexdigest()


def file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_members(zf: zipfile.ZipFile) -> list[str]:
    names = zf.namelist()
    for name in names:
        p = pathlib.PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"unsafe zip member: {name}")
    return names


def verify_manifest(zf: zipfile.ZipFile, manifest: dict) -> None:
    available = set(zf.namelist())
    for item in manifest.get("files", []):
        name = item.get("name")
        if not isinstance(name, str) or name not in available:
            raise ValueError(f"manifest member missing: {name}")
        data = zf.read(name)
        expected_size = item.get("bytes")
        expected_sha = item.get("sha256")
        if expected_size is not None and len(data) != int(expected_size):
            raise ValueError(f"size mismatch: {name}")
        if expected_sha and hashlib.sha256(data).hexdigest() != expected_sha:
            raise ValueError(f"sha256 mismatch: {name}")


def extract_text(content: dict) -> str:
    ctype = str(content.get("content_type") or "")
    if ctype == "text":
        parts = content.get("parts") or []
        return "\n".join(str(x) for x in parts if isinstance(x, (str, int, float)))
    if ctype == "code":
        return str(content.get("text") or "")
    if ctype == "tether_browsing_display":
        return str(content.get("text") or content.get("result") or "")
    return ""


def classify(role: str, content_type: str) -> str:
    if role == "system" or content_type in HIDDEN_TYPES:
        return "hidden"
    if role == "tool":
        return "evidence"
    if role in {"user", "assistant"} and content_type == "text":
        return "dialogue"
    return "trace"


def normalize_semantic_content(content: dict):
    ctype = str(content.get("content_type") or "unknown")
    if ctype == "text":
        return {"parts": content.get("parts") or []}
    if ctype == "code":
        return {"text": content.get("text") or ""}
    if ctype == "tether_browsing_display":
        return {"text": content.get("text"), "result": content.get("result")}
    return content


def canonical_message_object(message: dict) -> dict:
    author = message.get("author") or {}
    content = message.get("content") or {}
    return {
        "version": CANONICAL_MESSAGE_VERSION,
        "author_role": str(author.get("role") or "unknown"),
        "content_type": str(content.get("content_type") or "unknown"),
        "content": normalize_semantic_content(content),
    }


def _page_time_bounds(messages: list[dict]) -> tuple[float | None, float | None]:
    values = [float(m["create_time"]) for m in messages if isinstance(m.get("create_time"), (int, float))]
    return (min(values), max(values)) if values else (None, None)


def _load_payload_pages(zf: zipfile.ZipFile, manifest: dict) -> list[dict]:
    manifest_names = [item.get("name") for item in manifest.get("files", [])]
    payload_names = [name for name in manifest_names if isinstance(name, str) and PAYLOAD_RE.match(name)]
    if not payload_names:
        raise ValueError("no conversation payloads found")
    pages = []
    for capture_sequence, name in enumerate(payload_names):
        obj = json.loads(zf.read(name).decode("utf-8"))
        messages = obj.get("messages") or []
        if not isinstance(messages, list):
            raise ValueError(f"messages must be a list: {name}")
        page_info = obj.get("page_info") or {}
        min_time, max_time = _page_time_bounds(messages)
        pages.append(
            {
                "capture_sequence": capture_sequence,
                "member_name": name,
                "object": obj,
                "messages": messages,
                "page_info": page_info,
                "min_create_time": min_time,
                "max_create_time": max_time,
            }
        )
    return pages


def _chronology_key(page: dict) -> tuple[int, float, int]:
    min_time = page["min_create_time"]
    if min_time is None:
        return (1, 0.0, -int(page["capture_sequence"]))
    return (0, float(min_time), int(page["capture_sequence"]))


def normalize_artifact(source: pathlib.Path) -> NormalizedArtifact:
    source = pathlib.Path(source)
    with zipfile.ZipFile(source) as zf:
        safe_members(zf)
        manifest = json.loads(zf.read("manifest.json"))
        verify_manifest(zf, manifest)
        raw_pages = _load_payload_pages(zf, manifest)

    session_ids = {
        str(page["object"].get("conversation_id"))
        for page in raw_pages
        if page["object"].get("conversation_id")
    }
    if not session_ids:
        raise ValueError("BLOCKED_UNRESOLVED_SESSION_ID: no stable conversation_id in artifact")
    if len(session_ids) != 1:
        raise ValueError("BLOCKED_MIXED_SESSION_ARTIFACT: multiple conversation_id values in artifact")
    session_id = next(iter(session_ids))

    detail = next(
        (
            page["object"]
            for page in raw_pages
            if str(page["object"].get("conversation_id") or "") == session_id
        ),
        {},
    )
    title = str(detail.get("title") or "")

    chronological_pages = sorted(raw_pages, key=_chronology_key)
    oldest = chronological_pages[0]
    newest = chronological_pages[-1]
    has_previous = bool((oldest["page_info"] or {}).get("has_previous_page"))
    has_next = bool((newest["page_info"] or {}).get("has_next_page"))
    coverage = "COMPLETE_EXPOSED_CONVERSATION" if not has_previous else "PARTIAL_SESSION_SLICE"

    normalized_pages = tuple(
        NormalizedPage(
            capture_sequence=int(page["capture_sequence"]),
            member_name=str(page["member_name"]),
            start_cursor=(page["page_info"] or {}).get("start_cursor"),
            end_cursor=(page["page_info"] or {}).get("end_cursor"),
            has_previous_page=bool((page["page_info"] or {}).get("has_previous_page")),
            has_next_page=bool((page["page_info"] or {}).get("has_next_page")),
            message_count=len(page["messages"]),
            min_create_time=page["min_create_time"],
            max_create_time=page["max_create_time"],
        )
        for page in raw_pages
    )

    canonical: dict[str, dict] = {}
    canonical_digests: dict[str, str] = {}
    sources: dict[str, list[MessageSource]] = {}
    anonymous_counter = 0
    duplicate_occurrences = 0
    message_occurrences = 0

    for page in raw_pages:
        for position, message in enumerate(page["messages"]):
            message_occurrences += 1
            raw_id = str(message.get("id") or "")
            canonical_digest = _sha256_json(canonical_message_object(message))
            source_digest = _sha256_json(message)
            if raw_id:
                key = f"id:{raw_id}"
                if key in canonical:
                    duplicate_occurrences += 1
                    if canonical_digests[key] != canonical_digest:
                        raise ValueError(f"conflicting duplicate message_id: {raw_id}")
                else:
                    canonical[key] = message
                    canonical_digests[key] = canonical_digest
            else:
                key = f"anon:{anonymous_counter}"
                anonymous_counter += 1
                canonical[key] = message
                canonical_digests[key] = canonical_digest
            sources.setdefault(key, []).append(
                MessageSource(
                    capture_sequence=int(page["capture_sequence"]),
                    page_position=position,
                    member_name=str(page["member_name"]),
                    source_object_sha256=source_digest,
                )
            )

    def message_order(item: tuple[str, dict]) -> tuple[int, float, int, int]:
        key, message = item
        create_time = message.get("create_time")
        first_source = min((src.capture_sequence, src.page_position) for src in sources[key])
        if isinstance(create_time, (int, float)):
            return (0, float(create_time), first_source[0], first_source[1])
        return (1, 0.0, first_source[0], first_source[1])

    normalized_messages = []
    for key, message in sorted(canonical.items(), key=message_order):
        author = message.get("author") or {}
        content = message.get("content") or {}
        role = str(author.get("role") or "unknown")
        content_type = str(content.get("content_type") or "unknown")
        create_time = message.get("create_time")
        normalized_messages.append(
            NormalizedMessage(
                message_id=(str(message.get("id")) if message.get("id") else None),
                role=role,
                content_type=content_type,
                search_class=classify(role, content_type),
                create_time=(float(create_time) if isinstance(create_time, (int, float)) else None),
                text=extract_text(content),
                canonical_message_sha256=canonical_digests[key],
                sources=tuple(sources[key]),
            )
        )

    return NormalizedArtifact(
        source=source,
        artifact_sha256=file_sha256(source),
        size_bytes=source.stat().st_size,
        source_schema=str(manifest.get("schema") or ""),
        source_adapter=str(manifest.get("source_adapter") or "barn-doctor"),
        session_id=session_id,
        title=title,
        coverage_state=coverage,
        has_previous_page=has_previous,
        has_next_page=has_next,
        pages=normalized_pages,
        messages=tuple(normalized_messages),
        message_occurrences=message_occurrences,
        duplicate_message_occurrences=duplicate_occurrences,
    )
