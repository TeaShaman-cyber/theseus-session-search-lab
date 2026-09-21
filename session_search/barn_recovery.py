from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import zipfile

from .artifact import PAYLOAD_RE, file_sha256, normalize_artifact, safe_members, verify_manifest

SCHEMA = "theseus.session-search.barn-doctor-recovery.v1"
RECEIPT_SCHEMA = "theseus.session-search-recovery.v1"
PROVENANCE_RULE = "barn-doctor request_key + endpointClass + conversationId"
_MEMBER_RE = re.compile(
    r"^optional/conversation(?P<messages>-messages)?-(?P<request_key>[^/]+)\.bin$"
)
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _stable_json_bytes(obj: object) -> bytes:
    return json.dumps(
        obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, _FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def _atomic_write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _payload_descriptor(name: str) -> tuple[str, str]:
    match = _MEMBER_RE.match(name)
    if not match:
        raise ValueError(f"BLOCKED_UNSUPPORTED_RECOVERY_MEMBER: {name}")
    request_key = match.group("request_key")
    expected = "conversation_messages" if match.group("messages") else "conversation_detail"
    return request_key, expected


def _load_network_provenance(zf: zipfile.ZipFile) -> dict[tuple[str, str], set[str]]:
    if "network-events.jsonl" not in zf.namelist():
        raise ValueError("BLOCKED_RECOVERY_PROVENANCE_MISSING: network-events.jsonl")
    mapping: dict[tuple[str, str], set[str]] = {}
    for line_number, raw in enumerate(zf.read("network-events.jsonl").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except Exception as exc:
            raise ValueError(
                f"BLOCKED_RECOVERY_PROVENANCE_INVALID: network-events.jsonl:{line_number}"
            ) from exc
        data = event.get("data") if isinstance(event, dict) else None
        if not isinstance(data, dict):
            continue
        request_key = data.get("request_key")
        endpoint = data.get("endpointClass")
        conversation_id = data.get("conversationId")
        if not all(isinstance(v, str) and v for v in (request_key, endpoint, conversation_id)):
            continue
        mapping.setdefault((request_key, endpoint), set()).add(conversation_id)
    return mapping


def _owners_for_member(
    name: str, provenance: dict[tuple[str, str], set[str]]
) -> tuple[str, str, set[str]]:
    request_key, expected = _payload_descriptor(name)
    if expected == "conversation_messages":
        endpoints = ("conversation_messages",)
    else:
        endpoints = ("conversation_get", "conversation_init")
    owners: set[str] = set()
    matched_endpoint: str | None = None
    for endpoint in endpoints:
        values = provenance.get((request_key, endpoint), set())
        if values:
            owners.update(values)
            matched_endpoint = endpoint if matched_endpoint is None else matched_endpoint
    if not owners:
        raise ValueError(f"BLOCKED_UNRESOLVED_MEMBER_PROVENANCE: {name}")
    if len(owners) != 1:
        raise ValueError(f"BLOCKED_CONTRADICTORY_MEMBER_PROVENANCE: {name}")
    return request_key, matched_endpoint or endpoints[0], owners


def _choose_session(
    members: list[dict], explicit_session_id: str | None
) -> str:
    all_owners = {member["conversation_id"] for member in members}
    if explicit_session_id:
        if explicit_session_id not in all_owners:
            raise ValueError("BLOCKED_RECOVERY_TARGET_NOT_OBSERVED")
        return explicit_session_id

    message_owners = {
        member["conversation_id"]
        for member in members
        if member["endpoint_class"] == "conversation_messages"
    }
    if len(message_owners) == 1:
        return next(iter(message_owners))
    if len(message_owners) > 1:
        raise ValueError("BLOCKED_AMBIGUOUS_RECOVERY_SESSION")

    if len(all_owners) == 1:
        return next(iter(all_owners))
    if not all_owners:
        raise ValueError("BLOCKED_UNRESOLVED_RECOVERY_SESSION")
    raise ValueError("BLOCKED_AMBIGUOUS_RECOVERY_SESSION")


def recover_barn_doctor_capture(
    source: pathlib.Path,
    output_dir: pathlib.Path,
    session_id: str | None = None,
) -> tuple[pathlib.Path, pathlib.Path]:
    source = pathlib.Path(source)
    output_dir = pathlib.Path(output_dir)
    source_sha = file_sha256(source)

    with zipfile.ZipFile(source) as zf:
        safe_members(zf)
        manifest = json.loads(zf.read("manifest.json"))
        verify_manifest(zf, manifest)
        provenance = _load_network_provenance(zf)
        manifest_names = [item.get("name") for item in manifest.get("files", [])]
        payload_names = [
            name for name in manifest_names
            if isinstance(name, str) and PAYLOAD_RE.match(name)
        ]
        if not payload_names:
            raise ValueError("BLOCKED_RECOVERY_NO_CONVERSATION_PAYLOADS")

        members: list[dict] = []
        for name in payload_names:
            request_key, endpoint_class, owners = _owners_for_member(name, provenance)
            owner = next(iter(owners))
            raw = zf.read(name)
            try:
                obj = json.loads(raw)
            except Exception as exc:
                raise ValueError(f"BLOCKED_RECOVERY_PAYLOAD_INVALID: {name}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"BLOCKED_RECOVERY_PAYLOAD_INVALID: {name}")
            embedded = obj.get("conversation_id")
            if embedded and str(embedded) != owner:
                raise ValueError(f"BLOCKED_PAYLOAD_PROVENANCE_MISMATCH: {name}")
            members.append(
                {
                    "name": name,
                    "request_key": request_key,
                    "endpoint_class": endpoint_class,
                    "conversation_id": owner,
                    "raw": raw,
                    "object": obj,
                }
            )

    target_session = _choose_session(members, session_id)
    selected = [member for member in members if member["conversation_id"] == target_session]
    excluded = [member for member in members if member["conversation_id"] != target_session]
    if not selected:
        raise ValueError("BLOCKED_RECOVERY_TARGET_HAS_NO_MEMBERS")

    derived_members: list[tuple[str, bytes]] = []
    member_provenance: list[dict] = []
    for member in selected:
        obj = member["object"]
        if not obj.get("conversation_id"):
            obj = dict(obj)
            obj["conversation_id"] = target_session
            data = _stable_json_bytes(obj)
        else:
            data = member["raw"]
        derived_members.append((member["name"], data))
        member_provenance.append(
            {
                "member": member["name"],
                "request_key": member["request_key"],
                "endpoint_class": member["endpoint_class"],
                "conversation_id": target_session,
            }
        )

    derived_manifest = {
        "schema": SCHEMA,
        "source_adapter": "barn-doctor-recovery",
        "source_capture_sha256": source_sha,
        "selected_session_id": target_session,
        "provenance_rule": PROVENANCE_RULE,
        "files": [
            {
                "name": name,
                "bytes": len(data),
                "sha256": _sha256(data),
            }
            for name, data in derived_members
        ],
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        _zip_write(zf, "manifest.json", _stable_json_bytes(derived_manifest))
        for name, data in derived_members:
            _zip_write(zf, name, data)
    archive = buffer.getvalue()
    safe_session = _SAFE_ID.sub("_", target_session).strip("._") or "session"
    artifact_path = output_dir / f"barn-recovery-{safe_session}-{source_sha[:16]}.zip"
    _atomic_write(artifact_path, archive)

    normalized = normalize_artifact(artifact_path)
    if normalized.session_id != target_session:
        raise ValueError("BLOCKED_RECOVERY_POSTCONDITION_SESSION_MISMATCH")

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "source_sha256": source_sha,
        "derived_sha256": _sha256(archive),
        "selected_session_id": target_session,
        "included_members": [member["name"] for member in selected],
        "excluded_members": [member["name"] for member in excluded],
        "provenance_rule": PROVENANCE_RULE,
        "member_provenance": member_provenance,
    }
    receipt_path = artifact_path.with_suffix(".receipt.json")
    _atomic_write(receipt_path, _stable_json_bytes(receipt) + b"\n")
    return artifact_path, receipt_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recover one proven session from a Barn Doctor capture using request provenance."
    )
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--session-id")
    args = parser.parse_args(argv)
    try:
        artifact_path, receipt_path = recover_barn_doctor_capture(
            args.source, args.output_dir, args.session_id
        )
    except Exception as exc:
        print(f"BARN RECOVERY FAILED: {exc}")
        return 1
    print(
        json.dumps(
            {
                "artifact": str(artifact_path),
                "receipt": str(receipt_path),
            },
            ensure_ascii=False,
            sort_keys=True,
         )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
