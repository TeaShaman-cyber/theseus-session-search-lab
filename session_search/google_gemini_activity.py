from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import hashlib
import html.parser
import io
import json
import os
import pathlib
import re
import shutil
import sqlite3
import tarfile
import tempfile
import urllib.parse
import uuid
import zipfile


SCHEMA = "theseus.session-search.google-gemini-myactivity-child.v1"
ADAPTER = "google-gemini-myactivity"
SNAPSHOT_SCOPE = "OFFICIAL_MY_ACTIVITY_EXPOSED_SNAPSHOT"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_PROMPT_PREFIX = "Отправлен запрос "
_RU_TIMESTAMP_RE = re.compile(
    r"\b(?P<day>\d{1,2})\s+"
    r"(?P<month>[а-яё]+)\.?\s+"
    r"(?P<year>20\d{2})\s+г\.,\s+"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})\s+"
    r"(?P<zone>[A-Z]{2,5})\b",
    re.IGNORECASE,
)
_RU_MONTHS = {
    "янв": 1,
    "февр": 2,
    "мар": 3,
    "апр": 4,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сент": 9,
    "окт": 10,
    "нояб": 11,
    "дек": 12,
}
_ZONE_OFFSETS = {
    "UTC": 0,
    "GMT": 0,
    "CET": 1,
    "CEST": 2,
}
_GEMINI_PRODUCT_NAMES = {"Приложения Gemini", "Gemini Apps"}
_AI_MODE_PRODUCT_NAMES = {"Режим ИИ", "AI Mode"}


def _stable_json_bytes(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_tar_members(tf: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    result: dict[str, tarfile.TarInfo] = {}
    for member in tf.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"BLOCKED_UNSAFE_GEMINI_TAKEOUT_MEMBER: {member.name}")
        if member.isfile():
            result[member.name] = member
    return result


def _find_product_activity_member(
    members: dict[str, tarfile.TarInfo],
    product_names: set[str],
) -> str | None:
    matches: list[str] = []
    for name in members:
        path = pathlib.PurePosixPath(name)
        if path.suffix.lower() != ".html" or len(path.parts) < 4:
            continue
        if path.parent.name not in product_names:
            continue
        if path.name not in {"МоиДействия.html", "MyActivity.html", "My Activity.html"}:
            continue
        matches.append(name)
    if len(matches) > 1:
        raise ValueError(
            "BLOCKED_AMBIGUOUS_GEMINI_ACTIVITY_MEMBER: " + ", ".join(sorted(matches))
        )
    return matches[0] if matches else None


class _TextAccumulator:
    _BLOCK_START = {
        "p",
        "div",
        "section",
        "article",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
    }
    _BLOCK_END = _BLOCK_START | {"li", "ol", "ul"}

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.preformatted: list[str] = []
        self._pre_depth = 0

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        tag = tag.lower()
        if tag == "pre":
            self.parts.append("\n")
            self._pre_depth += 1
            return
        if self._pre_depth:
            if tag == "br":
                self.parts.append("\n")
            return
        if tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "tr":
            self.parts.append("\n")
        elif tag in self._BLOCK_START:
            self.parts.append("\n")
        elif tag == "img":
            alt = attrs.get("alt")
            if alt:
                self.parts.append(str(alt))

    def end(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "pre":
            if self._pre_depth:
                self._pre_depth -= 1
            self.parts.append("\n")
            return
        if self._pre_depth:
            return
        if tag in {"td", "th"}:
            self.parts.append(" ")
        elif tag == "tr" or tag in self._BLOCK_END:
            self.parts.append("\n")

    def data(self, value: str) -> None:
        if self._pre_depth:
            index = len(self.preformatted)
            token = f"\ue000PRE{index}\ue001"
            self.preformatted.append(
                value.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
            )
            self.parts.append(token)
            return
        self.parts.append(value)

    @property
    def in_preformatted(self) -> bool:
        return self._pre_depth > 0

    def text(self) -> str:
        raw = (
            "".join(self.parts)
            .replace("\xa0", " ")
            .replace("\u202f", " ")
            .replace("\u2009", " ")
            .replace("\u2007", " ")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
        lines: list[str] = []
        for line in raw.split("\n"):
            normalized = re.sub(r"[ \t\f\v]+", " ", line).strip()
            if normalized:
                lines.append(normalized)
        result = "\n".join(lines).strip()
        for index, value in enumerate(self.preformatted):
            result = result.replace(f"\ue000PRE{index}\ue001", value)
        return result.strip("\n")


@dataclasses.dataclass
class _Cell:
    css_class: str
    segments: list[_TextAccumulator] = dataclasses.field(
        default_factory=lambda: [_TextAccumulator()]
    )
    hrefs: list[str] = dataclasses.field(default_factory=list)

    @property
    def current_segment(self) -> _TextAccumulator:
        return self.segments[-1]

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        if tag.lower() == "br" and not self.current_segment.in_preformatted:
            self.segments.append(_TextAccumulator())
            return
        self.current_segment.start(tag, attrs)

    def end(self, tag: str) -> None:
        self.current_segment.end(tag)

    def data(self, value: str) -> None:
        self.current_segment.data(value)

    def segment_texts(self) -> list[str]:
        return [segment.text() for segment in self.segments]

    def text(self) -> str:
        return "\n".join(part for part in self.segment_texts() if part).strip()


@dataclasses.dataclass
class _Card:
    index: int
    cells: list[_Cell] = dataclasses.field(default_factory=list)


class _MyActivityParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[_Card] = []
        self.current_card: _Card | None = None
        self.current_cell: _Cell | None = None
        self.div_depth = 0
        self.outer_depth: int | None = None
        self.cell_depth: int | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = {str(k): str(v or "") for k, v in attrs}
        css_class = attrs_dict.get("class", "")
        if tag.lower() == "div":
            self.div_depth += 1
            if self.current_card is None and "outer-cell" in css_class.split():
                self.current_card = _Card(index=len(self.cards))
                self.cards.append(self.current_card)
                self.outer_depth = self.div_depth
                return
            if (
                self.current_card is not None
                and self.current_cell is None
                and "content-cell" in css_class.split()
            ):
                self.current_cell = _Cell(css_class=css_class)
                self.current_card.cells.append(self.current_cell)
                self.cell_depth = self.div_depth
                return

        if self.current_cell is not None:
            if tag.lower() == "a":
                href = attrs_dict.get("href")
                if href:
                    self.current_cell.hrefs.append(href)
            self.current_cell.start(tag, attrs_dict)

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if self.current_cell is not None:
            if not (lower == "div" and self.cell_depth == self.div_depth):
                self.current_cell.end(tag)

        if lower == "div":
            if self.current_cell is not None and self.cell_depth == self.div_depth:
                self.current_cell = None
                self.cell_depth = None
            if self.current_card is not None and self.outer_depth == self.div_depth:
                self.current_card = None
                self.outer_depth = None
            self.div_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.current_cell is not None:
            self.current_cell.data(data)


@dataclasses.dataclass(frozen=True)
class _AttachmentRef:
    href: str
    decoded_href: str
    resolution: str
    member_name: str | None = None
    bytes: int | None = None
    sha256: str | None = None

    def as_metadata(self) -> dict:
        result = {
            "href": self.href,
            "decoded_href": self.decoded_href,
            "resolution": self.resolution,
        }
        if self.member_name is not None:
            result["member_name"] = self.member_name
        if self.bytes is not None:
            result["bytes"] = self.bytes
        if self.sha256 is not None:
            result["sha256"] = self.sha256
        return result


@dataclasses.dataclass(frozen=True)
class _PromptCard:
    card_index: int
    app_ids: tuple[str, ...]
    timestamp_raw: str
    timestamp_epoch: float
    user_text: str
    assistant_text: str
    attachment_refs: tuple[_AttachmentRef, ...]


@dataclasses.dataclass(frozen=True)
class _Analysis:
    source_export_sha256: str
    source_export_bytes: int
    source_member: str
    source_member_sha256: str
    ignored_ai_mode_members: int
    gemini_activity_cards: int
    prompt_cards: tuple[_PromptCard, ...]
    other_activity_cards: int
    unsupported_prompt_cards: int
    resolved_attachment_ref_count: int
    unresolved_attachment_ref_count: int


@dataclasses.dataclass(frozen=True)
class _MaterializedSnapshot:
    artifacts: tuple[pathlib.Path, ...]
    analysis: _Analysis
    resolved_conversation_count: int
    unresolved_artifact_count: int
    message_count: int
    user_message_count: int
    assistant_message_count: int


def _parse_timestamp(value: str) -> float:
    match = _RU_TIMESTAMP_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"BLOCKED_UNSUPPORTED_GEMINI_ACTIVITY_TIMESTAMP: {value}")
    month_token = match.group("month").lower()
    month = _RU_MONTHS.get(month_token)
    if month is None:
        raise ValueError(f"BLOCKED_UNSUPPORTED_GEMINI_ACTIVITY_MONTH: {month_token}")
    zone_token = match.group("zone").upper()
    offset = _ZONE_OFFSETS.get(zone_token)
    if offset is None:
        raise ValueError(f"BLOCKED_UNSUPPORTED_GEMINI_ACTIVITY_ZONE: {zone_token}")
    tz = dt.timezone(dt.timedelta(hours=offset))
    value_dt = dt.datetime(
        int(match.group("year")),
        month,
        int(match.group("day")),
        int(match.group("hour")),
        int(match.group("minute")),
        int(match.group("second")),
        tzinfo=tz,
    )
    return value_dt.timestamp()


def _body_cell(card: _Card) -> _Cell | None:
    candidates = [
        cell
        for cell in card.cells
        if "mdl-cell--6-col" in cell.css_class.split()
        and "mdl-typography--text-right" not in cell.css_class.split()
    ]
    return candidates[0] if len(candidates) == 1 else None


def _caption_cells(card: _Card) -> list[_Cell]:
    return [
        cell
        for cell in card.cells
        if "mdl-cell--12-col" in cell.css_class.split()
        and "mdl-typography--caption" in cell.css_class.split()
    ]


def _app_ids_from_caption(card: _Card) -> tuple[str, ...]:
    ids: list[str] = []
    for cell in _caption_cells(card):
        for href in cell.hrefs:
            match = re.search(r"gemini\.google\.com/app/([A-Za-z0-9_-]+)", href)
            if match and match.group(1) not in ids:
                ids.append(match.group(1))
    return tuple(ids)


def _is_local_href(href: str) -> bool:
    parsed = urllib.parse.urlparse(href)
    return not parsed.scheme and not href.startswith("#")


def _analyze_takeout_bytes(raw_export: bytes) -> _Analysis:
    parent_sha = _sha256(raw_export)
    with tarfile.open(fileobj=io.BytesIO(raw_export), mode="r:gz") as tf:
        members = _safe_tar_members(tf)
        source_member = _find_product_activity_member(members, _GEMINI_PRODUCT_NAMES)
        if source_member is None:
            raise ValueError("BLOCKED_GEMINI_ACTIVITY_MEMBER_NOT_FOUND")

        source_product = pathlib.PurePosixPath(source_member).parent.name
        if source_product != "Приложения Gemini":
            raise ValueError(
                "BLOCKED_UNSUPPORTED_GEMINI_ACTIVITY_LOCALE: "
                f"{source_product}"
            )

        ai_member = _find_product_activity_member(members, _AI_MODE_PRODUCT_NAMES)
        source_file = tf.extractfile(members[source_member])
        if source_file is None:
            raise ValueError("BLOCKED_GEMINI_ACTIVITY_MEMBER_UNREADABLE")
        source_bytes = source_file.read()
        try:
            source_html = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("BLOCKED_GEMINI_ACTIVITY_HTML_NOT_UTF8") from exc

        parser = _MyActivityParser()
        parser.feed(source_html)
        parser.close()

        # Lazy exact member byte cache for attachment provenance.
        member_bytes: dict[str, bytes] = {}

        def bytes_for(name: str) -> bytes:
            if name not in member_bytes:
                fh = tf.extractfile(members[name])
                if fh is None:
                    raise ValueError(f"BLOCKED_GEMINI_ATTACHMENT_UNREADABLE: {name}")
                member_bytes[name] = fh.read()
            return member_bytes[name]

        # _resolve_attachment accepts a mapping, so expose a lazy-compatible dict by
        # pre-reading only referenced members after resolution candidate discovery.
        prompt_cards: list[_PromptCard] = []
        other_activity_cards = 0
        unsupported_prompt_cards = 0
        resolved_refs = 0
        unresolved_refs = 0

        for card in parser.cards:
            body = _body_cell(card)
            if body is None:
                other_activity_cards += 1
                continue
            segments = body.segment_texts()
            main = "\n".join(part for part in segments if part).strip()
            if not main.startswith(_PROMPT_PREFIX):
                other_activity_cards += 1
                continue

            timestamp_indexes = [
                index
                for index, segment in enumerate(segments)
                if _RU_TIMESTAMP_RE.fullmatch(segment.strip())
            ]
            if not timestamp_indexes:
                unsupported_prompt_cards += 1
                continue
            timestamp_index = timestamp_indexes[0]
            timestamp_raw = segments[timestamp_index].strip()
            timestamp_epoch = _parse_timestamp(timestamp_raw)

            pre_timestamp = "\n".join(
                part for part in segments[:timestamp_index] if part
            ).strip()
            post_timestamp = "\n".join(
                part for part in segments[timestamp_index + 1 :] if part
            ).strip()
            if not pre_timestamp.startswith(_PROMPT_PREFIX):
                unsupported_prompt_cards += 1
                continue
            user_text = pre_timestamp[len(_PROMPT_PREFIX) :].strip()
            assistant_text = post_timestamp
            if not user_text:
                unsupported_prompt_cards += 1
                continue

            app_ids = _app_ids_from_caption(card)
            attachment_refs: list[_AttachmentRef] = []
            for href in body.hrefs:
                if not _is_local_href(href):
                    continue
                decoded = urllib.parse.unquote(href)
                path = pathlib.PurePosixPath(decoded)
                base = pathlib.PurePosixPath(source_member).parent
                candidate = str(base / path)
                resolved_name = candidate if candidate in members else None
                if resolved_name is None:
                    lower_matches = [name for name in members if name.lower() == candidate.lower()]
                    if len(lower_matches) == 1:
                        resolved_name = lower_matches[0]
                if resolved_name is None:
                    ref = _AttachmentRef(
                        href=href,
                        decoded_href=decoded,
                        resolution="UNRESOLVED_ATTACHMENT_REF",
                    )
                    unresolved_refs += 1
                else:
                    data = bytes_for(resolved_name)
                    ref = _AttachmentRef(
                        href=href,
                        decoded_href=decoded,
                        resolution="RESOLVED",
                        member_name=resolved_name,
                        bytes=len(data),
                        sha256=_sha256(data),
                    )
                    resolved_refs += 1
                attachment_refs.append(ref)

            prompt_cards.append(
                _PromptCard(
                    card_index=card.index,
                    app_ids=app_ids,
                    timestamp_raw=timestamp_raw,
                    timestamp_epoch=timestamp_epoch,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    attachment_refs=tuple(attachment_refs),
                )
            )

    return _Analysis(
        source_export_sha256=parent_sha,
        source_export_bytes=len(raw_export),
        source_member=source_member,
        source_member_sha256=_sha256(source_bytes),
        ignored_ai_mode_members=(1 if ai_member is not None else 0),
        gemini_activity_cards=len(parser.cards),
        prompt_cards=tuple(prompt_cards),
        other_activity_cards=other_activity_cards,
        unsupported_prompt_cards=unsupported_prompt_cards,
        resolved_attachment_ref_count=resolved_refs,
        unresolved_attachment_ref_count=unresolved_refs,
    )


def _activity_signature(card: _PromptCard) -> str:
    attachments = [
        {
            "href": ref.href,
            "decoded_href": ref.decoded_href,
            "resolution": ref.resolution,
            "member_name": ref.member_name,
            "bytes": ref.bytes,
            "sha256": ref.sha256,
        }
        for ref in card.attachment_refs
    ]
    return _sha256(
        _stable_json_bytes(
            [
                list(card.app_ids),
                card.timestamp_raw,
                card.user_text,
                card.assistant_text,
                attachments,
            ]
        )
    )


def _activity_occurrence_id(
    session_id: str,
    card: _PromptCard,
    occurrence_rank: int,
) -> str:
    digest = _sha256(
        _stable_json_bytes(
            [session_id, _activity_signature(card), int(occurrence_rank)]
        )
    )
    return f"google-gemini-myactivity-occurrence:{digest}"


def _message_id(
    session_id: str,
    occurrence_id: str,
    role: str,
    text: str,
) -> str:
    digest = _sha256(
        _stable_json_bytes(
            [
                session_id,
                occurrence_id,
                role,
                text,
            ]
        )
    )
    return f"google-gemini-myactivity-message:{digest}"


def _unresolved_session_id(
    card: _PromptCard,
    occurrence_rank: int = 0,
) -> str:
    digest = _sha256(
        _stable_json_bytes(
            [
                _activity_signature(card),
                int(occurrence_rank),
            ]
        )
    )
    return f"gemini-unresolved:{digest[:32]}"


def _messages_for_cards(
    *,
    session_id: str,
    cards: list[_PromptCard],
    source_member: str,
    source_member_sha256: str,
) -> list[dict]:
    ordered_cards = sorted(cards, key=lambda card: (card.timestamp_epoch, -card.card_index))
    messages: list[dict] = []
    order = 0
    signature_counts: collections.Counter[str] = collections.Counter()
    for card in ordered_cards:
        signature = _activity_signature(card)
        occurrence_rank = int(signature_counts[signature])
        signature_counts[signature] += 1
        occurrence_id = _activity_occurrence_id(
            session_id,
            card,
            occurrence_rank,
        )
        common = {
            "gemini_activity_source_member": source_member,
            "gemini_activity_source_member_sha256": source_member_sha256,
            "gemini_activity_card_index": card.card_index,
            "gemini_activity_timestamp_raw": card.timestamp_raw,
            "gemini_activity_related_app_ids": list(card.app_ids),
            "gemini_activity_occurrence_id": occurrence_id,
            "gemini_activity_occurrence_rank": occurrence_rank,
        }
        user_metadata = {
            **common,
            "gemini_activity_time_semantics": "USER_PROMPT_ACTIVITY_TIME",
            "gemini_attachment_refs": [ref.as_metadata() for ref in card.attachment_refs],
            "session_search_order": order,
        }
        messages.append(
            {
                "id": _message_id(
                    session_id,
                    occurrence_id,
                    "user",
                    card.user_text,
                ),
                "author": {"role": "user"},
                "create_time": card.timestamp_epoch,
                "content": {"content_type": "text", "parts": [card.user_text]},
                "metadata": user_metadata,
            }
        )
        order += 1
        if card.assistant_text:
            assistant_metadata = {
                **common,
                "gemini_activity_time_semantics": "RESPONSE_TIME_NOT_EXPOSED",
                "session_search_order": order,
            }
            messages.append(
                {
                    "id": _message_id(
                        session_id,
                        occurrence_id,
                        "assistant",
                        card.assistant_text,
                    ),
                    "author": {"role": "assistant"},
                    "create_time": None,
                    "content": {"content_type": "text", "parts": [card.assistant_text]},
                    "metadata": assistant_metadata,
                }
            )
            order += 1
    return messages


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
            raise ValueError("BLOCKED_GOOGLE_GEMINI_MYACTIVITY: content-address collision")
        return
    temp = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError(
                    "BLOCKED_GOOGLE_GEMINI_MYACTIVITY: concurrent content-address collision"
                )
            return
        os.replace(temp, target)
        if target.read_bytes() != data:
            raise ValueError("BLOCKED_GOOGLE_GEMINI_MYACTIVITY: published artifact mismatch")
    finally:
        if temp.exists():
            temp.unlink()


def _materialize_takeout_snapshot(
    source: pathlib.Path,
    output_dir: pathlib.Path,
) -> _MaterializedSnapshot:
    source = pathlib.Path(source)
    output_dir = pathlib.Path(output_dir)
    raw_export = source.read_bytes()
    analysis = _analyze_takeout_bytes(raw_export)

    resolved: dict[str, list[_PromptCard]] = collections.defaultdict(list)
    unresolved: list[_PromptCard] = []
    for card in analysis.prompt_cards:
        if len(card.app_ids) == 1:
            resolved[card.app_ids[0]].append(card)
        else:
            unresolved.append(card)

    specs: list[tuple[str, str | None, list[_PromptCard], str]] = []
    for app_id in sorted(resolved):
        session_id = f"gemini:{app_id}"
        specs.append(
            (
                session_id,
                app_id,
                resolved[app_id],
                f"Gemini Apps {app_id}",
            )
        )
    unresolved_counts: collections.Counter[str] = collections.Counter()
    for card in sorted(
        unresolved,
        key=lambda item: (item.timestamp_epoch, -item.card_index),
    ):
        signature = _activity_signature(card)
        occurrence_rank = int(unresolved_counts[signature])
        unresolved_counts[signature] += 1
        session_id = _unresolved_session_id(card, occurrence_rank)
        specs.append(
            (
                session_id,
                None,
                [card],
                "Gemini unresolved activity",
            )
        )

    staging_root = pathlib.Path(
        tempfile.mkdtemp(prefix="session-search-google-gemini-myactivity-stage-")
    )
    pending: list[tuple[pathlib.Path, pathlib.Path]] = []
    outputs: list[pathlib.Path] = []
    message_count = 0
    user_count = 0
    assistant_count = 0
    try:
        for session_id, source_conversation_id, cards, title in specs:
            messages = _messages_for_cards(
                session_id=session_id,
                cards=cards,
                source_member=analysis.source_member,
                source_member_sha256=analysis.source_member_sha256,
            )
            if not messages:
                continue
            message_count += len(messages)
            user_count += sum((m.get("author") or {}).get("role") == "user" for m in messages)
            assistant_count += sum(
                (m.get("author") or {}).get("role") == "assistant" for m in messages
            )
            payload = {
                "conversation_id": session_id,
                "title": title,
                "messages": messages,
                "page_info": {
                    "has_previous_page": True,
                    "has_next_page": False,
                },
                "gemini_source_app_id": source_conversation_id,
                "gemini_snapshot_scope": SNAPSHOT_SCOPE,
                "gemini_activity_card_count": len(cards),
            }
            payload_bytes = _stable_json_bytes(payload)
            safe_session = _SAFE_ID.sub("_", session_id).strip("._")[:120] or "conversation"
            member = f"optional/conversation-google-gemini-myactivity-{safe_session}.bin"
            manifest = {
                "schema": SCHEMA,
                "source_adapter": ADAPTER,
                "source_export_sha256": analysis.source_export_sha256,
                "source_export_bytes": analysis.source_export_bytes,
                "source_member": analysis.source_member,
                "source_member_sha256": analysis.source_member_sha256,
                "session_id": session_id,
                "snapshot_scope": SNAPSHOT_SCOPE,
                "activity_card_count": len(cards),
                "files": [
                    {
                        "name": member,
                        "bytes": len(payload_bytes),
                        "sha256": _sha256(payload_bytes),
                    }
                ],
            }
            if source_conversation_id is not None:
                manifest["source_conversation_id"] = source_conversation_id
            else:
                manifest["related_app_ids"] = sorted(
                    {app_id for card in cards for app_id in card.app_ids}
                )
                manifest["unresolved_reason"] = (
                    "AMBIGUOUS_MULTI_APP_ACTIVITY"
                    if any(len(card.app_ids) > 1 for card in cards)
                    else "MISSING_APP_ID_ACTIVITY"
                )
            archive_bytes = _portable_zip_bytes(member, payload_bytes, manifest)
            archive_sha = _sha256(archive_bytes)
            target = output_dir / (
                f"google-gemini-myactivity-{safe_session}-{archive_sha[:16]}.zip"
            )
            stage_path = staging_root / f"{len(pending):08d}.zip"
            stage_path.write_bytes(archive_bytes)
            pending.append((target, stage_path))
            outputs.append(target)

        output_dir.mkdir(parents=True, exist_ok=True)
        for target, stage_path in pending:
            _publish_content_addressed(target, stage_path.read_bytes())
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)

    return _MaterializedSnapshot(
        artifacts=tuple(outputs),
        analysis=analysis,
        resolved_conversation_count=len(resolved),
        unresolved_artifact_count=len(unresolved),
        message_count=message_count,
        user_message_count=user_count,
        assistant_message_count=assistant_count,
    )


def materialize_takeout(source: pathlib.Path, output_dir: pathlib.Path) -> list[pathlib.Path]:
    return list(_materialize_takeout_snapshot(source, output_dir).artifacts)


def _receipt_from_snapshot(snapshot: _MaterializedSnapshot) -> dict:
    analysis = snapshot.analysis
    resolved_prompt_cards = sum(len(cards) for _, cards in _resolved_groups(analysis).items())
    ambiguous = sum(len(card.app_ids) > 1 for card in analysis.prompt_cards)
    missing = sum(len(card.app_ids) == 0 for card in analysis.prompt_cards)
    return {
        "schema": SCHEMA,
        "source_adapter": ADAPTER,
        "snapshot_scope": SNAPSHOT_SCOPE,
        "source_export_sha256": analysis.source_export_sha256,
        "source_export_bytes": analysis.source_export_bytes,
        "source_member": analysis.source_member,
        "source_member_sha256": analysis.source_member_sha256,
        "ignored_ai_mode_members": analysis.ignored_ai_mode_members,
        "gemini_activity_cards": analysis.gemini_activity_cards,
        "prompt_cards": len(analysis.prompt_cards),
        "resolved_prompt_cards": resolved_prompt_cards,
        "ambiguous_multi_app_prompt_cards": ambiguous,
        "missing_app_id_prompt_cards": missing,
        "unsupported_prompt_cards": analysis.unsupported_prompt_cards,
        "other_activity_cards": analysis.other_activity_cards,
        "resolved_conversation_count": snapshot.resolved_conversation_count,
        "unresolved_artifact_count": snapshot.unresolved_artifact_count,
        "artifact_count": len(snapshot.artifacts),
        "message_count": snapshot.message_count,
        "user_message_count": snapshot.user_message_count,
        "assistant_message_count": snapshot.assistant_message_count,
        "attachment_ref_count": (
            analysis.resolved_attachment_ref_count
            + analysis.unresolved_attachment_ref_count
        ),
        "resolved_attachment_ref_count": analysis.resolved_attachment_ref_count,
        "unresolved_attachment_ref_count": analysis.unresolved_attachment_ref_count,
    }


def _resolved_groups(analysis: _Analysis) -> dict[str, list[_PromptCard]]:
    result: dict[str, list[_PromptCard]] = collections.defaultdict(list)
    for card in analysis.prompt_cards:
        if len(card.app_ids) == 1:
            result[card.app_ids[0]].append(card)
    return result


def inspect_takeout(source: pathlib.Path) -> dict:
    with tempfile.TemporaryDirectory(
        prefix="session-search-google-gemini-myactivity-inspect-"
    ) as td:
        snapshot = _materialize_takeout_snapshot(source, pathlib.Path(td))
    return _receipt_from_snapshot(snapshot)


def ingest_takeout(source: pathlib.Path, corpus_root: pathlib.Path) -> dict:
    from .corpus_store import ingest_many

    source = pathlib.Path(source)
    with tempfile.TemporaryDirectory(
        prefix="session-search-google-gemini-myactivity-"
    ) as td:
        snapshot = _materialize_takeout_snapshot(source, pathlib.Path(td))
        child_ids = {
            str(path): f"google-gemini-myactivity-child-sha256:{_sha256(path.read_bytes())}"
            for path in snapshot.artifacts
        }
        result = ingest_many(list(snapshot.artifacts), pathlib.Path(corpus_root))
        for item in result.get("results", []):
            source_value = item.get("source")
            if source_value in child_ids:
                item["source"] = child_ids[source_value]
    return {**result, **_receipt_from_snapshot(snapshot)}


def reconcile_notebooklm_rescue(
    source: pathlib.Path,
    corpus_root: pathlib.Path,
) -> dict:
    from .artifact import normalize_artifact

    corpus_root = pathlib.Path(corpus_root)
    db = corpus_root / "corpus.sqlite3"
    if not db.exists():
        return {
            "rescue_artifacts": 0,
            "rescue_message_occurrences": 0,
            "official_artifacts": 0,
            "official_message_occurrences": 0,
            "rescue_sessions_unique_exact_best_match": 0,
            "rescue_sessions_ambiguous_exact_best_match": 0,
            "rescue_sessions_zero_exact_overlap": 0,
            "rescue_sessions_full_subset_of_one_official": 0,
            "rescue_message_occurrences_with_any_official_exact_match": 0,
            "routing_action": "NONE_EVIDENCE_PRESERVED",
        }

    with tempfile.TemporaryDirectory(
        prefix="session-search-google-gemini-myactivity-reconcile-"
    ) as td:
        official_snapshot = _materialize_takeout_snapshot(source, pathlib.Path(td))
        official = [normalize_artifact(path) for path in official_snapshot.artifacts]

    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT sha256 FROM artifacts "
            "WHERE source_adapter=? ORDER BY sha256",
            ("notebooklm-preserved-gemini",),
        ).fetchall()
    finally:
        con.close()

    rescue = []
    for (sha256_value,) in rows:
        path = corpus_root / "artifacts" / "sha256" / f"{sha256_value}.zip"
        if not path.exists():
            raise ValueError(
                "BLOCKED_NOTEBOOKLM_RECONCILIATION_MISSING_ACCEPTED_BLOB: "
                f"{sha256_value}"
            )
        rescue.append(normalize_artifact(path))

    official_sets = {
        artifact.session_id: {
            message.canonical_message_sha256 for message in artifact.messages
        }
        for artifact in official
    }
    rescue_sets = {
        artifact.session_id: {
            message.canonical_message_sha256 for message in artifact.messages
        }
        for artifact in rescue
    }

    unique_best = 0
    ambiguous_best = 0
    zero = 0
    full_subset = 0
    matched_occurrences = 0

    all_official_digests = set().union(*official_sets.values()) if official_sets else set()

    for artifact in rescue:
        rescue_set = rescue_sets[artifact.session_id]
        scores: list[tuple[int, str]] = []
        for session_id, official_set in official_sets.items():
            overlap = len(rescue_set & official_set)
            if overlap:
                scores.append((overlap, session_id))
        if not scores:
            zero += 1
        else:
            best = max(score for score, _ in scores)
            best_sessions = [session_id for score, session_id in scores if score == best]
            if len(best_sessions) == 1:
                unique_best += 1
                if rescue_set <= official_sets[best_sessions[0]]:
                    full_subset += 1
            else:
                ambiguous_best += 1

        matched_occurrences += sum(
            message.canonical_message_sha256 in all_official_digests
            for message in artifact.messages
        )

    return {
        "rescue_artifacts": len(rescue),
        "rescue_message_occurrences": sum(len(artifact.messages) for artifact in rescue),
        "official_artifacts": len(official),
        "official_message_occurrences": sum(
            len(artifact.messages) for artifact in official
        ),
        "rescue_sessions_unique_exact_best_match": unique_best,
        "rescue_sessions_ambiguous_exact_best_match": ambiguous_best,
        "rescue_sessions_zero_exact_overlap": zero,
        "rescue_sessions_full_subset_of_one_official": full_subset,
        "rescue_message_occurrences_with_any_official_exact_match": matched_occurrences,
        "routing_action": "NONE_EVIDENCE_PRESERVED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import official Google My Activity / Gemini Apps Takeout data."
    )
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument(
        "--existing-corpus",
        type=pathlib.Path,
        help="Optionally include aggregate NotebookLM rescue reconciliation in --inspect output.",
    )
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output-dir", type=pathlib.Path)
    destination.add_argument("--corpus", type=pathlib.Path)
    destination.add_argument("--inspect", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.inspect:
            receipt = inspect_takeout(args.source)
            if args.existing_corpus is not None:
                receipt["notebooklm_reconciliation"] = reconcile_notebooklm_rescue(
                    args.source, args.existing_corpus
                )
            print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
            return 0
        if args.corpus is not None:
            result = ingest_takeout(args.source, args.corpus)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0 if result.get("status") == "COMPLETE" else 1
        outputs = materialize_takeout(args.source, args.output_dir)
    except Exception as exc:
        print(f"GOOGLE GEMINI MYACTIVITY IMPORT FAILED: {exc}")
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
