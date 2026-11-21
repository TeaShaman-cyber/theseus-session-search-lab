from __future__ import annotations

import argparse
import dataclasses
import datetime
import html
import io
import json
import pathlib
import posixpath
import re
import tarfile
import tempfile
import urllib.parse

from .notebooklm_gemini import (
    _html_to_text,
    _portable_zip_bytes,
    _publish_content_addressed,
    _sha256,
    _stable_json_bytes,
)


SCHEMA = "theseus.session-search.google-my-activity-gemini-child.v1"
ADAPTER = "google-my-activity-gemini"
PRESERVATION_SCOPE = "PROVIDER_ACTIVITY_HISTORY_PARTIAL"

_CARD_MARKER = '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">'
_DIV_TOKEN_RE = re.compile(r"<div\b[^>]*>|</div\s*>", re.I)
_CLASS_RE = re.compile(r'class="([^"]*)"', re.I)
_HREF_RE = re.compile(r'href="([^"]+)"', re.I)
_APP_ID_RE = re.compile(r"gemini\.google\.com/app/([A-Za-z0-9_-]+)")
_TIMESTAMP_RE = re.compile(
    r"(?m)^(\d{1,2})\s+([а-яё]+)\.?\s+(\d{4})\s+г\.,\s+"
    r"(\d{2}):(\d{2}):(\d{2})\s+([A-Z]{2,5})$"
)

_MONTHS = {
    "янв": 1,
    "января": 1,
    "февр": 2,
    "февраля": 2,
    "мар": 3,
    "марта": 3,
    "апр": 4,
    "апреля": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июня": 6,
    "июл": 7,
    "июля": 7,
    "авг": 8,
    "августа": 8,
    "сент": 9,
    "сентября": 9,
    "окт": 10,
    "октября": 10,
    "нояб": 11,
    "ноября": 11,
    "дек": 12,
    "декабря": 12,
}
_ZONE_OFFSETS = {
    "UTC": 0,
    "GMT": 0,
    "CET": 1,
    "CEST": 2,
}


@dataclasses.dataclass(frozen=True)
class _AttachmentRef:
    href: str
    member: str | None
    bytes: int | None
    sha256: str | None


@dataclasses.dataclass(frozen=True)
class _RequestEvent:
    source_card_index: int
    conversation_ids: tuple[str, ...]
    timestamp_raw: str
    create_time: float
    prompt: str
    response: str
    attachments: tuple[_AttachmentRef, ...]


@dataclasses.dataclass(frozen=True)
class _Session:
    session_id: str
    source_conversation_id: str | None
    grouping: str
    related_conversation_ids: tuple[str, ...]
    events: tuple[_RequestEvent, ...]


@dataclasses.dataclass(frozen=True)
class _Snapshot:
    artifacts: tuple[pathlib.Path, ...]
    source_export_sha256: str
    source_export_bytes: int
    activity_member: str
    activity_member_sha256: str
    activity_card_count: int
    request_count: int
    provider_session_count: int
    ambiguous_event_count: int
    orphan_event_count: int
    artifact_count: int
    message_count: int
    user_message_count: int
    assistant_message_count: int
    prompt_only_count: int
    attachment_ref_count: int
    activity_categories: dict[str, int]


def _safe_tar_members(tf: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = tf.getmembers()
    for member in members:
        p = pathlib.PurePosixPath(member.name)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"unsafe tar member: {member.name}")
    return members


def _matching_div_inner(source: str, start: int) -> tuple[str, int]:
    first = _DIV_TOKEN_RE.match(source, start)
    if first is None or first.group(0).lower().startswith("</"):
        raise ValueError("BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: malformed div start")
    depth = 0
    for token in _DIV_TOKEN_RE.finditer(source, start):
        raw = token.group(0)
        if raw.lower().startswith("</"):
            depth -= 1
            if depth == 0:
                return source[first.end() : token.start()], token.end()
        else:
            depth += 1
    raise ValueError("BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: unterminated div")


def _field_inner(card: str, *, require: set[str], exclude: set[str] | None = None) -> str | None:
    exclude = exclude or set()
    for match in re.finditer(r"<div\b[^>]*>", card, re.I):
        class_match = _CLASS_RE.search(match.group(0))
        if class_match is None:
            continue
        classes = set(class_match.group(1).split())
        if not require.issubset(classes) or classes.intersection(exclude):
            continue
        inner, _ = _matching_div_inner(card, match.start())
        return inner
    return None


def _find_activity_member(tf: tarfile.TarFile, members: list[tarfile.TarInfo]) -> tuple[tarfile.TarInfo, bytes]:
    candidates: list[tuple[tarfile.TarInfo, bytes]] = []
    for member in members:
        if not member.isfile() or pathlib.PurePosixPath(member.name).suffix.lower() != ".html":
            continue
        handle = tf.extractfile(member)
        if handle is None:
            continue
        raw = handle.read()
        if b"gemini.google.com/app/" in raw and b"outer-cell mdl-cell" in raw:
            candidates.append((member, raw))
    if len(candidates) != 1:
        raise ValueError(
            "BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: "
            f"expected exactly one Gemini activity HTML, got {len(candidates)}"
        )
    return candidates[0]


def _parse_timestamp(raw: str) -> float:
    match = _TIMESTAMP_RE.fullmatch(raw)
    if match is None:
        raise ValueError(
            "BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: timestamp format changed"
        )
    day, month_raw, year, hour, minute, second, zone = match.groups()
    month = _MONTHS.get(month_raw.lower())
    offset = _ZONE_OFFSETS.get(zone)
    if month is None or offset is None:
        raise ValueError(
            "BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: unsupported timestamp locale/zone"
        )
    dt = datetime.datetime(
        int(year),
        month,
        int(day),
        int(hour),
        int(minute),
        int(second),
        tzinfo=datetime.timezone(datetime.timedelta(hours=offset)),
    )
    return dt.timestamp()


def _caption_ids(caption_html: str) -> tuple[str, ...]:
    result: list[str] = []
    for href in _HREF_RE.findall(caption_html):
        match = _APP_ID_RE.search(html.unescape(href))
        if match and match.group(1) not in result:
            result.append(match.group(1))
    return tuple(result)


def _activity_category(main_text: str) -> str:
    if main_text.startswith("Отправлен запрос "):
        return "request"
    if main_text.startswith("Ваш отзыв:"):
        return "feedback"
    if main_text.startswith("Создан"):
        return "created"
    if main_text.startswith("Использ"):
        return "used"
    if main_text.startswith("Удал"):
        return "deleted"
    return "other"


def _attachment_hrefs(left_html: str) -> tuple[str, ...]:
    result: list[str] = []
    for raw_href in _HREF_RE.findall(left_html):
        href = html.unescape(raw_href)
        parsed = urllib.parse.urlsplit(href)
        if parsed.scheme or parsed.netloc or href.startswith("#"):
            continue
        value = urllib.parse.unquote(parsed.path)
        if value and value not in result:
            result.append(value)
    return tuple(result)


def _resolve_attachment(
    tf: tarfile.TarFile,
    members_by_name: dict[str, tarfile.TarInfo],
    activity_member: str,
    href: str,
    cache: dict[str, _AttachmentRef],
) -> _AttachmentRef:
    if href in cache:
        return cache[href]
    parent = str(pathlib.PurePosixPath(activity_member).parent)
    candidate = posixpath.normpath(posixpath.join(parent, href))
    parent_prefix = parent.rstrip("/") + "/"
    if not candidate.startswith(parent_prefix):
        raise ValueError(
            "BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: unsafe attachment reference"
        )
    member = members_by_name.get(candidate)
    if member is None or not member.isfile():
        ref = _AttachmentRef(href=href, member=None, bytes=None, sha256=None)
        cache[href] = ref
        return ref
    handle = tf.extractfile(member)
    if handle is None:
        raise ValueError(
            "BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: attachment member unreadable"
        )
    data = handle.read()
    ref = _AttachmentRef(
        href=href,
        member=candidate,
        bytes=len(data),
        sha256=_sha256(data),
    )
    cache[href] = ref
    return ref


def _parse_request(
    tf: tarfile.TarFile,
    members_by_name: dict[str, tarfile.TarInfo],
    activity_member: str,
    source_card_index: int,
    left_html: str,
    caption_html: str,
    attachment_cache: dict[str, _AttachmentRef],
) -> _RequestEvent:
    main_text = _html_to_text(left_html)
    timestamps = list(_TIMESTAMP_RE.finditer(main_text))
    if len(timestamps) != 1:
        raise ValueError(
            "BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: "
            f"request timestamp cardinality changed at card {source_card_index}"
        )
    stamp = timestamps[0]
    before = main_text[: stamp.start()]
    if not before.startswith("Отправлен запрос "):
        raise ValueError(
            "BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: request prefix changed"
        )
    prompt = before[len("Отправлен запрос ") :].strip()
    if not prompt:
        raise ValueError(
            "BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: empty request prompt"
        )
    response = main_text[stamp.end() :].strip()
    timestamp_raw = stamp.group(0)
    refs = tuple(
        _resolve_attachment(
            tf,
            members_by_name,
            activity_member,
            href,
            attachment_cache,
        )
        for href in _attachment_hrefs(left_html)
    )
    return _RequestEvent(
        source_card_index=source_card_index,
        conversation_ids=_caption_ids(caption_html),
        timestamp_raw=timestamp_raw,
        create_time=_parse_timestamp(timestamp_raw),
        prompt=prompt,
        response=response,
        attachments=refs,
    )


def _event_identity(event: _RequestEvent) -> str:
    obj = {
        "conversation_ids": list(event.conversation_ids),
        "timestamp_raw": event.timestamp_raw,
        "prompt": event.prompt,
        "response": event.response,
        "attachments": [dataclasses.asdict(ref) for ref in event.attachments],
    }
    return _sha256(_stable_json_bytes(obj))


def _message_id(session_id: str, event: _RequestEvent, role: str, text: str) -> str:
    return "google-gemini-message:" + _sha256(
        _stable_json_bytes(
            [session_id, _event_identity(event), role, text]
        )
    )


def _group_sessions(events: list[_RequestEvent]) -> list[_Session]:
    single: dict[str, list[_RequestEvent]] = {}
    special: list[_Session] = []
    for event in events:
        if len(event.conversation_ids) == 1:
            app_id = event.conversation_ids[0]
            single.setdefault(app_id, []).append(event)
            continue
        identity = _event_identity(event)
        prefix = "multi" if event.conversation_ids else "orphan"
        special.append(
            _Session(
                session_id=f"google-gemini-{prefix}:{identity[:32]}",
                source_conversation_id=None,
                grouping=(
                    "ambiguous_multi_id"
                    if event.conversation_ids
                    else "no_provider_id"
                ),
                related_conversation_ids=event.conversation_ids,
                events=(event,),
            )
        )

    sessions: list[_Session] = []
    for app_id, grouped in sorted(single.items()):
        # Google Takeout activity cards are newest-first; source_card_index therefore
        # decreases as time moves forward. Sort by provider time and use the reversed
        # source order only as a deterministic same-second tie breaker.
        ordered = tuple(
            sorted(grouped, key=lambda event: (event.create_time, -event.source_card_index))
        )
        sessions.append(
            _Session(
                session_id=f"google-gemini:{app_id}",
                source_conversation_id=app_id,
                grouping="single_provider_id",
                related_conversation_ids=(app_id,),
                events=ordered,
            )
        )
    sessions.extend(sorted(special, key=lambda item: item.session_id))
    return sessions


def _payload_for_session(session: _Session) -> dict:
    messages: list[dict] = []
    attachment_refs: list[dict] = []
    seen_attachments: set[tuple] = set()
    order = 0
    for event in session.events:
        event_metadata = {
            "google_my_activity_source_card_index": event.source_card_index,
            "google_my_activity_timestamp_raw": event.timestamp_raw,
            "google_my_activity_conversation_ids": list(event.conversation_ids),
            "google_my_activity_grouping": session.grouping,
        }
        event_refs = [dataclasses.asdict(ref) for ref in event.attachments]
        if event_refs:
            event_metadata["google_my_activity_attachment_refs"] = event_refs
            for ref in event_refs:
                key = tuple(sorted(ref.items()))
                if key not in seen_attachments:
                    attachment_refs.append(ref)
                    seen_attachments.add(key)

        messages.append(
            {
                "id": _message_id(session.session_id, event, "user", event.prompt),
                "author": {"role": "user"},
                "create_time": event.create_time,
                "content": {"content_type": "text", "parts": [event.prompt]},
                "metadata": {
                    **event_metadata,
                    "session_search_order": order,
                },
            }
        )
        order += 1
        if event.response:
            messages.append(
                {
                    "id": _message_id(
                        session.session_id,
                        event,
                        "assistant",
                        event.response,
                    ),
                    "author": {"role": "assistant"},
                    "create_time": event.create_time,
                    "content": {"content_type": "text", "parts": [event.response]},
                    "metadata": {
                        **event_metadata,
                        "session_search_order": order,
                    },
                }
            )
            order += 1

    return {
        "conversation_id": session.session_id,
        "title": (
            f"Gemini conversation {session.source_conversation_id}"
            if session.source_conversation_id
            else "Gemini My Activity ambiguous event"
        ),
        "messages": messages,
        "page_info": {
            "has_previous_page": True,
            "has_next_page": False,
        },
        "google_my_activity_grouping": session.grouping,
        "google_my_activity_source_conversation_id": session.source_conversation_id,
        "google_my_activity_related_conversation_ids": list(
            session.related_conversation_ids
        ),
        "google_my_activity_attachment_refs": attachment_refs,
        "google_my_activity_preservation_scope": PRESERVATION_SCOPE,
    }


def _materialize_snapshot(
    source: pathlib.Path,
    output_dir: pathlib.Path,
) -> _Snapshot:
    source = pathlib.Path(source)
    output_dir = pathlib.Path(output_dir)

    # Capture one immutable byte snapshot so source hash and parsed contents cannot
    # diverge if the original path changes while conversion is running.
    raw_export = source.read_bytes()
    export_sha = _sha256(raw_export)

    with tarfile.open(fileobj=io.BytesIO(raw_export), mode="r:gz") as tf:
        members = _safe_tar_members(tf)
        members_by_name = {member.name: member for member in members}
        activity_info, activity_raw = _find_activity_member(tf, members)
        activity_sha = _sha256(activity_raw)
        text = activity_raw.decode("utf-8")
        chunks = text.split(_CARD_MARKER)[1:]
        if not chunks:
            raise ValueError(
                "BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: no activity cards"
            )

        categories: dict[str, int] = {}
        events: list[_RequestEvent] = []
        attachment_cache: dict[str, _AttachmentRef] = {}

        for source_card_index, chunk in enumerate(chunks):
            left_html = _field_inner(
                chunk,
                require={
                    "content-cell",
                    "mdl-cell",
                    "mdl-cell--6-col",
                    "mdl-typography--body-1",
                },
                exclude={"mdl-typography--text-right"},
            )
            caption_html = _field_inner(
                chunk,
                require={
                    "content-cell",
                    "mdl-cell",
                    "mdl-cell--12-col",
                    "mdl-typography--caption",
                },
            )
            if left_html is None:
                continue
            main_text = _html_to_text(left_html)
            category = _activity_category(main_text)
            categories[category] = categories.get(category, 0) + 1
            if category != "request":
                continue
            if caption_html is None:
                raise ValueError(
                    "BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI: request caption missing"
                )
            events.append(
                _parse_request(
                    tf,
                    members_by_name,
                    activity_info.name,
                    source_card_index,
                    left_html,
                    caption_html,
                    attachment_cache,
                )
            )

        sessions = _group_sessions(events)
        pending: list[tuple[pathlib.Path, bytes]] = []
        outputs: list[pathlib.Path] = []
        for session in sessions:
            payload = _payload_for_session(session)
            payload_bytes = _stable_json_bytes(payload)
            member_name = (
                "optional/conversation-google-my-activity-gemini-"
                + _sha256(session.session_id.encode("utf-8"))[:16]
                + ".bin"
            )
            manifest = {
                "schema": SCHEMA,
                "source_adapter": ADAPTER,
                "source_export_sha256": export_sha,
                "source_export_bytes": len(raw_export),
                "source_member": activity_info.name,
                "source_member_sha256": activity_sha,
                "session_id": session.session_id,
                "source_conversation_id": session.source_conversation_id,
                "preservation_scope": PRESERVATION_SCOPE,
                "grouping": session.grouping,
                "related_conversation_ids": list(session.related_conversation_ids),
                "files": [
                    {
                        "name": member_name,
                        "bytes": len(payload_bytes),
                        "sha256": _sha256(payload_bytes),
                    }
                ],
            }
            archive_bytes = _portable_zip_bytes(
                member_name,
                payload_bytes,
                manifest,
            )
            target = output_dir / (
                "gemini-my-activity-"
                + _sha256(session.session_id.encode("utf-8"))[:16]
                + "-"
                + _sha256(archive_bytes)[:16]
                + ".zip"
            )
            pending.append((target, archive_bytes))
            outputs.append(target)

    output_dir.mkdir(parents=True, exist_ok=True)
    for target, archive_bytes in pending:
        _publish_content_addressed(target, archive_bytes)

    user_count = sum(len(session.events) for session in sessions)
    assistant_count = sum(
        1 for session in sessions for event in session.events if event.response
    )
    prompt_only = sum(
        1 for session in sessions for event in session.events if not event.response
    )
    attachment_count = sum(
        len(event.attachments) for session in sessions for event in session.events
    )
    provider_sessions = sum(
        1 for session in sessions if session.grouping == "single_provider_id"
    )
    ambiguous = sum(
        1 for session in sessions if session.grouping == "ambiguous_multi_id"
    )
    orphan = sum(1 for session in sessions if session.grouping == "no_provider_id")

    return _Snapshot(
        artifacts=tuple(outputs),
        source_export_sha256=export_sha,
        source_export_bytes=len(raw_export),
        activity_member=activity_info.name,
        activity_member_sha256=activity_sha,
        activity_card_count=len(chunks),
        request_count=len(events),
        provider_session_count=provider_sessions,
        ambiguous_event_count=ambiguous,
        orphan_event_count=orphan,
        artifact_count=len(outputs),
        message_count=user_count + assistant_count,
        user_message_count=user_count,
        assistant_message_count=assistant_count,
        prompt_only_count=prompt_only,
        attachment_ref_count=attachment_count,
        activity_categories=dict(sorted(categories.items())),
    )


def materialize_takeout(
    source: pathlib.Path,
    output_dir: pathlib.Path,
) -> list[pathlib.Path]:
    return list(_materialize_snapshot(source, output_dir).artifacts)


def inspect_takeout(source: pathlib.Path) -> dict:
    with tempfile.TemporaryDirectory(
        prefix="session-search-google-my-activity-gemini-inspect-"
    ) as td:
        snapshot = _materialize_snapshot(source, pathlib.Path(td))
    return {
        "schema": SCHEMA,
        "source_adapter": ADAPTER,
        "preservation_scope": PRESERVATION_SCOPE,
        "source_export_sha256": snapshot.source_export_sha256,
        "source_export_bytes": snapshot.source_export_bytes,
        "activity_member": snapshot.activity_member,
        "activity_member_sha256": snapshot.activity_member_sha256,
        "activity_card_count": snapshot.activity_card_count,
        "request_count": snapshot.request_count,
        "provider_session_count": snapshot.provider_session_count,
        "ambiguous_event_count": snapshot.ambiguous_event_count,
        "orphan_event_count": snapshot.orphan_event_count,
        "artifact_count": snapshot.artifact_count,
        "message_count": snapshot.message_count,
        "user_message_count": snapshot.user_message_count,
        "assistant_message_count": snapshot.assistant_message_count,
        "prompt_only_count": snapshot.prompt_only_count,
        "attachment_ref_count": snapshot.attachment_ref_count,
        "activity_categories": snapshot.activity_categories,
    }


def ingest_takeout(source: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    from .corpus_store import ingest_many

    with tempfile.TemporaryDirectory(
        prefix="session-search-google-my-activity-gemini-"
    ) as td:
        snapshot = _materialize_snapshot(source, pathlib.Path(td))
        child_ids = {
            str(path): "google-my-activity-gemini-child-sha256:"
            + _sha256(path.read_bytes())
            for path in snapshot.artifacts
        }
        result = ingest_many(list(snapshot.artifacts), pathlib.Path(corpus_root))
        for item in result.get("results", []):
            source_value = item.get("source")
            if source_value in child_ids:
                item["source"] = child_ids[source_value]
    return {
        **result,
        "artifact_count": snapshot.artifact_count,
        "provider_session_count": snapshot.provider_session_count,
        "ambiguous_event_count": snapshot.ambiguous_event_count,
        "orphan_event_count": snapshot.orphan_event_count,
        "request_count": snapshot.request_count,
        "message_count": snapshot.message_count,
        "user_message_count": snapshot.user_message_count,
        "assistant_message_count": snapshot.assistant_message_count,
        "prompt_only_count": snapshot.prompt_only_count,
        "attachment_ref_count": snapshot.attachment_ref_count,
        "source_export_sha256": snapshot.source_export_sha256,
        "preservation_scope": PRESERVATION_SCOPE,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import official Google My Activity Gemini Takeout data."
    )
    parser.add_argument("source", type=pathlib.Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output-dir", type=pathlib.Path)
    destination.add_argument("--corpus", type=pathlib.Path)
    destination.add_argument("--inspect", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.inspect:
            print(
                json.dumps(
                    inspect_takeout(args.source),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.corpus is not None:
            result = ingest_takeout(args.source, args.corpus)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result.get("status") == "COMPLETE" else 1
        outputs = materialize_takeout(args.source, args.output_dir)
    except Exception as exc:
        print(f"GOOGLE MY ACTIVITY GEMINI IMPORT FAILED: {exc}")
        return 1

    print(
        json.dumps(
            {"artifacts": [str(path) for path in outputs], "count": len(outputs)},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
