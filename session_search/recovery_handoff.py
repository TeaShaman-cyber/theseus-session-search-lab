from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import tempfile
from typing import Any

from session_search.corpus_lock import pin_corpus
from session_search.corpus_store import CORPUS_SCHEMA_VERSION

RECEIPT_SCHEMA = "theseus.session-search-recovery-receipt.v1"
LOCATOR_SCHEMA = "theseus.session-search-recovery-locator.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_RE = re.compile(r"^[0-9a-f]{40,64}$")


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(value: Any, code: str) -> str:
    text = str(value)
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(code)
    return text


def _require_positive_int(value: Any, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(code)
    return value


def _require_nonempty(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(code)
    return value


def _projection_schema_version(db_path: pathlib.Path) -> str:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro&immutable=1", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM corpus_meta WHERE key='schema_version'"
        ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("RECOVERY_PROJECTION_SCHEMA_UNREADABLE") from exc
    finally:
        conn.close()
    if row is None:
        raise ValueError("RECOVERY_PROJECTION_SCHEMA_MISSING")
    return str(row[0])


def _artifact_descriptor(path: pathlib.Path, drive_file_id: str, *, role: str) -> dict:
    artifact = pathlib.Path(path).expanduser().resolve()
    if not artifact.is_file():
        raise ValueError("RECOVERY_ARTIFACT_MISSING")
    return {
        "role": role,
        "drive_file_id": _require_nonempty(drive_file_id, "RECOVERY_DRIVE_FILE_ID_INVALID"),
        "sha256": _sha256_file(artifact),
        "size_bytes": artifact.stat().st_size,
    }


def build_receipt(
    *,
    projection_transport_path: pathlib.Path,
    projection_drive_file_id: str,
    projection_db_path: pathlib.Path,
    evidence_path: pathlib.Path,
    evidence_drive_file_id: str,
    source_commit: str,
    accepted_set_sha256: str,
    verification: dict,
    source_watermark: dict | None = None,
    projection_encoding: str = "zstd",
) -> dict:
    if not isinstance(verification, dict) or verification.get("status") != "VERIFIED":
        raise ValueError("RECOVERY_VERIFICATION_NOT_ACCEPTED")
    if not _GIT_RE.fullmatch(str(source_commit)):
        raise ValueError("RECOVERY_SOURCE_COMMIT_INVALID")
    accepted_set = _require_sha256(
        accepted_set_sha256, "RECOVERY_ACCEPTED_SET_DIGEST_INVALID"
    )

    projection_db = pathlib.Path(projection_db_path).expanduser().resolve()
    if not projection_db.is_file():
        raise ValueError("RECOVERY_PROJECTION_DB_MISSING")
    schema = _projection_schema_version(projection_db)
    if schema != CORPUS_SCHEMA_VERSION:
        raise ValueError("RECOVERY_PROJECTION_SCHEMA_MISMATCH")

    projection = _artifact_descriptor(
        projection_transport_path,
        projection_drive_file_id,
        role="projection_transport",
    )
    projection.update(
        {
            "decoded_sha256": _sha256_file(projection_db),
            "decoded_size_bytes": projection_db.stat().st_size,
            "sqlite_schema_version": schema,
            "encoding": _require_nonempty(
                projection_encoding, "RECOVERY_PROJECTION_ENCODING_INVALID"
            ),
        }
    )
    evidence = _artifact_descriptor(
        evidence_path,
        evidence_drive_file_id,
        role="accepted_evidence",
    )

    payload = {
        "schema": RECEIPT_SCHEMA,
        "source_commit": str(source_commit),
        "accepted_set_sha256": accepted_set,
        "verification": dict(verification),
        "artifacts": {
            "projection_transport": projection,
            "accepted_evidence": evidence,
        },
    }
    if source_watermark is not None:
        if not isinstance(source_watermark, dict):
            raise ValueError("RECOVERY_SOURCE_WATERMARK_INVALID")
        payload["source_watermark"] = dict(source_watermark)
    validate_receipt(payload)
    return payload


def validate_receipt(receipt: dict) -> dict:
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("RECOVERY_RECEIPT_SCHEMA_MISMATCH")
    if not _GIT_RE.fullmatch(str(receipt.get("source_commit", ""))):
        raise ValueError("RECOVERY_SOURCE_COMMIT_INVALID")
    _require_sha256(
        receipt.get("accepted_set_sha256"),
        "RECOVERY_ACCEPTED_SET_DIGEST_INVALID",
    )
    verification = receipt.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "VERIFIED":
        raise ValueError("RECOVERY_VERIFICATION_NOT_ACCEPTED")

    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("RECOVERY_ARTIFACTS_INVALID")
    for key in ("projection_transport", "accepted_evidence"):
        descriptor = artifacts.get(key)
        if not isinstance(descriptor, dict):
            raise ValueError("RECOVERY_ARTIFACT_DESCRIPTOR_MISSING")
        if descriptor.get("role") != key:
            raise ValueError("RECOVERY_ARTIFACT_ROLE_MISMATCH")
        _require_nonempty(
            descriptor.get("drive_file_id"), "RECOVERY_DRIVE_FILE_ID_INVALID"
        )
        _require_sha256(
            descriptor.get("sha256"), "RECOVERY_ARTIFACT_DIGEST_INVALID"
        )
        _require_positive_int(
            descriptor.get("size_bytes"), "RECOVERY_ARTIFACT_SIZE_INVALID"
        )

    projection = artifacts["projection_transport"]
    _require_sha256(
        projection.get("decoded_sha256"),
        "RECOVERY_DECODED_DIGEST_INVALID",
    )
    _require_positive_int(
        projection.get("decoded_size_bytes"),
        "RECOVERY_DECODED_SIZE_INVALID",
    )
    if projection.get("sqlite_schema_version") != CORPUS_SCHEMA_VERSION:
        raise ValueError("RECOVERY_PROJECTION_SCHEMA_MISMATCH")
    _require_nonempty(
        projection.get("encoding"), "RECOVERY_PROJECTION_ENCODING_INVALID"
    )
    if "source_watermark" in receipt and not isinstance(receipt["source_watermark"], dict):
        raise ValueError("RECOVERY_SOURCE_WATERMARK_INVALID")
    return receipt


def write_private_json(path: pathlib.Path, payload: dict) -> pathlib.Path:
    target = pathlib.Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return target


def build_locator(
    receipt_path: pathlib.Path,
    *,
    receipt_drive_file_id: str,
) -> dict:
    path = pathlib.Path(receipt_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("RECOVERY_RECEIPT_MISSING")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("RECOVERY_RECEIPT_INVALID_JSON") from exc
    validate_receipt(receipt)
    return {
        "schema": LOCATOR_SCHEMA,
        "receipt": {
            "drive_file_id": _require_nonempty(
                receipt_drive_file_id, "RECOVERY_RECEIPT_DRIVE_ID_INVALID"
            ),
            "sha256": _sha256_file(path),
            "size_bytes": path.stat().st_size,
        },
    }


def validate_locator(locator: dict) -> dict:
    if not isinstance(locator, dict) or locator.get("schema") != LOCATOR_SCHEMA:
        raise ValueError("RECOVERY_LOCATOR_SCHEMA_MISMATCH")
    receipt = locator.get("receipt")
    if not isinstance(receipt, dict):
        raise ValueError("RECOVERY_LOCATOR_RECEIPT_MISSING")
    _require_nonempty(
        receipt.get("drive_file_id"), "RECOVERY_RECEIPT_DRIVE_ID_INVALID"
    )
    _require_sha256(
        receipt.get("sha256"), "RECOVERY_RECEIPT_DIGEST_INVALID"
    )
    _require_positive_int(
        receipt.get("size_bytes"), "RECOVERY_RECEIPT_SIZE_INVALID"
    )
    return locator


def load_verified_handoff(
    locator_path: pathlib.Path,
    receipt_path: pathlib.Path,
) -> dict:
    try:
        locator = json.loads(
            pathlib.Path(locator_path).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("RECOVERY_LOCATOR_INVALID") from exc
    validate_locator(locator)

    receipt_file = pathlib.Path(receipt_path).expanduser().resolve()
    if not receipt_file.is_file():
        raise ValueError("RECOVERY_RECEIPT_MISSING")
    expected = locator["receipt"]
    if receipt_file.stat().st_size != int(expected["size_bytes"]):
        raise ValueError("RECOVERY_RECEIPT_SIZE_MISMATCH")
    if _sha256_file(receipt_file) != expected["sha256"]:
        raise ValueError("RECOVERY_RECEIPT_DIGEST_MISMATCH")
    try:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("RECOVERY_RECEIPT_INVALID_JSON") from exc
    validate_receipt(receipt)
    return {"locator": locator, "receipt": receipt}


def verify_artifact_file(path: pathlib.Path, descriptor: dict) -> pathlib.Path:
    artifact = pathlib.Path(path).expanduser().resolve()
    if not artifact.is_file():
        raise ValueError("RECOVERY_ARTIFACT_MISSING")
    if artifact.stat().st_size != int(descriptor["size_bytes"]):
        raise ValueError("RECOVERY_ARTIFACT_SIZE_MISMATCH")
    if _sha256_file(artifact) != descriptor["sha256"]:
        raise ValueError("RECOVERY_ARTIFACT_DIGEST_MISMATCH")
    return artifact

def verify_decoded_projection(
    db_path: pathlib.Path,
    descriptor: dict,
) -> pathlib.Path:
    db = pathlib.Path(db_path).expanduser().resolve()
    if not db.is_file():
        raise ValueError("RECOVERY_PROJECTION_DB_MISSING")
    if db.stat().st_size != int(descriptor["decoded_size_bytes"]):
        raise ValueError("RECOVERY_DECODED_SIZE_MISMATCH")
    if _sha256_file(db) != descriptor["decoded_sha256"]:
        raise ValueError("RECOVERY_DECODED_DIGEST_MISMATCH")
    if _projection_schema_version(db) != descriptor["sqlite_schema_version"]:
        raise ValueError("RECOVERY_PROJECTION_SCHEMA_MISMATCH")
    return db


def verify_local_recovery(
    *,
    locator_path: pathlib.Path,
    receipt_path: pathlib.Path,
    projection_transport_path: pathlib.Path,
    evidence_path: pathlib.Path,
    projection_db_path: pathlib.Path,
    corpus_lock_out: pathlib.Path | None = None,
) -> dict:
    handoff = load_verified_handoff(locator_path, receipt_path)
    receipt = handoff["receipt"]
    artifacts = receipt["artifacts"]

    verify_artifact_file(
        projection_transport_path,
        artifacts["projection_transport"],
    )
    verify_artifact_file(
        evidence_path,
        artifacts["accepted_evidence"],
    )
    db = verify_decoded_projection(
        projection_db_path,
        artifacts["projection_transport"],
    )
    if corpus_lock_out is not None:
        pin_corpus(db, corpus_lock_out)
    return {
        "status": "VERIFIED",
        "sqlite_schema_version": artifacts["projection_transport"][
            "sqlite_schema_version"
        ],
        "corpus_lock_created": corpus_lock_out is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a private Session Search recovery handoff after transport. "
            "The command never prints Drive IDs or content digests."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    make_receipt = sub.add_parser("build-receipt")
    make_receipt.add_argument("--spec", type=pathlib.Path, required=True)
    make_receipt.add_argument("--out", type=pathlib.Path, required=True)

    make_locator = sub.add_parser("build-locator")
    make_locator.add_argument("--spec", type=pathlib.Path, required=True)
    make_locator.add_argument("--out", type=pathlib.Path, required=True)

    verify = sub.add_parser("verify-local")
    verify.add_argument("--locator", type=pathlib.Path, required=True)
    verify.add_argument("--receipt", type=pathlib.Path, required=True)
    verify.add_argument("--projection-transport", type=pathlib.Path, required=True)
    verify.add_argument("--evidence", type=pathlib.Path, required=True)
    verify.add_argument("--projection-db", type=pathlib.Path, required=True)
    verify.add_argument("--corpus-lock-out", type=pathlib.Path)

    args = parser.parse_args()
    if args.command == "build-receipt":
        try:
            spec = json.loads(args.spec.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("RECOVERY_BUILD_SPEC_INVALID") from exc
        receipt = build_receipt(
            projection_transport_path=pathlib.Path(spec["projection_transport_path"]),
            projection_drive_file_id=spec["projection_drive_file_id"],
            projection_db_path=pathlib.Path(spec["projection_db_path"]),
            evidence_path=pathlib.Path(spec["evidence_path"]),
            evidence_drive_file_id=spec["evidence_drive_file_id"],
            source_commit=spec["source_commit"],
            accepted_set_sha256=spec["accepted_set_sha256"],
            verification=spec["verification"],
            source_watermark=spec.get("source_watermark"),
            projection_encoding=spec.get("projection_encoding", "zstd"),
        )
        write_private_json(args.out, receipt)
        print(json.dumps({"status": "BUILT", "schema": RECEIPT_SCHEMA}, sort_keys=True))
        return 0

    if args.command == "build-locator":
        try:
            spec = json.loads(args.spec.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("RECOVERY_LOCATOR_SPEC_INVALID") from exc
        locator = build_locator(
            pathlib.Path(spec["receipt_path"]),
            receipt_drive_file_id=spec["receipt_drive_file_id"],
        )
        write_private_json(args.out, locator)
        print(json.dumps({"status": "BUILT", "schema": LOCATOR_SCHEMA}, sort_keys=True))
        return 0

    if args.command == "verify-local":
        result = verify_local_recovery(
            locator_path=args.locator,
            receipt_path=args.receipt,
            projection_transport_path=args.projection_transport,
            evidence_path=args.evidence,
            projection_db_path=args.projection_db,
            corpus_lock_out=args.corpus_lock_out,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())

