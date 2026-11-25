"""Local accepted-artifact membership receipts; not source freshness or message deltas."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .artifact_overlap import set_digest
from .corpus_store import (
    CorpusMutationLock,
    CorpusPaths,
    accepted_entry_digest,
    read_accepted_ledger,
    verify_projection_consistency,
)

SCHEMA = "theseus.session-search-accepted-membership.v1"
CURRENTNESS = "UNKNOWN_WITHOUT_SOURCE_WATERMARK"
SHA256 = re.compile(r"[0-9a-f]{64}")


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _scope(scope: Any) -> str:
    if not isinstance(scope, str) or not scope.strip() or scope != scope.strip():
        raise ValueError("INVALID_CORPUS_SCOPE")
    return scope


def _hash(value: Any) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError("INVALID_MEMBERSHIP_DIGEST")
    return value


def _validated_members(snapshot: Any) -> dict[str, str]:
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "schema", "scope", "currentness", "verification_level", "members",
        "accepted_set_sha256", "snapshot_sha256",
    }:
        raise ValueError("INVALID_MEMBERSHIP_RECEIPT")
    if (
        snapshot["schema"] != SCHEMA
        or snapshot["currentness"] != CURRENTNESS
        or snapshot["verification_level"] != "projection_consistency"
    ):
        raise ValueError("INVALID_MEMBERSHIP_RECEIPT")
    _scope(snapshot["scope"])
    members = snapshot["members"]
    if not isinstance(members, list):
        raise ValueError("INVALID_MEMBERSHIP_MEMBERS")
    result = {}
    for member in members:
        if not isinstance(member, dict) or set(member) != {"artifact_sha256", "ledger_sha256"}:
            raise ValueError("INVALID_MEMBERSHIP_MEMBER")
        sha = _hash(member["artifact_sha256"])
        if sha in result:
            raise ValueError("DUPLICATE_MEMBERSHIP_ARTIFACT")
        result[sha] = _hash(member["ledger_sha256"])
    if list(result) != sorted(result):
        raise ValueError("NONCANONICAL_MEMBERSHIP_ORDER")
    if snapshot["accepted_set_sha256"] != set_digest(result):
        raise ValueError("ACCEPTED_SET_DIGEST_MISMATCH")
    core = {k: v for k, v in snapshot.items() if k != "snapshot_sha256"}
    if snapshot["snapshot_sha256"] != _digest(core):
        raise ValueError("MEMBERSHIP_SNAPSHOT_DIGEST_MISMATCH")
    return result


def capture_accepted_membership(corpus_root: Path, *, scope: str) -> dict:
    """Capture local ledger membership while cooperating writers are excluded.

    Scope is an explicit caller-owned corpus identity, not an authorization token.
    The existing consistency check does not re-derive artifacts or prove upstream
    completeness. The returned receipt contains no message bodies or file paths.
    """
    scope = _scope(scope)
    paths = CorpusPaths.from_root(corpus_root)
    if not paths.accepted_ledger.is_dir() or not paths.db.is_file():
        raise ValueError("CORPUS_NOT_INITIALIZED")
    with CorpusMutationLock(paths, "accepted-membership-snapshot"):
        return _capture_locked(paths, scope)


def _capture_locked(paths: CorpusPaths, scope: str) -> dict:
    """Internal helper: caller holds the existing corpus mutation lock."""
    verification = verify_projection_consistency(paths.root)
    if verification.get("status") != "PROJECTION_CONSISTENT":
        raise ValueError("PROJECTION_NOT_CONSISTENT")
    ledger = read_accepted_ledger(paths)
    core = {
        "schema": SCHEMA,
        "scope": scope,
        "currentness": CURRENTNESS,
        "verification_level": "projection_consistency",
        "members": [
            {"artifact_sha256": sha, "ledger_sha256": accepted_entry_digest(entry)}
            for sha, entry in sorted(ledger.items())
        ],
        "accepted_set_sha256": set_digest(ledger),
    }
    snapshot = {**core, "snapshot_sha256": _digest(core)}
    _validated_members(snapshot)
    return snapshot

def compare_accepted_membership(previous: dict, current: dict) -> dict:
    """Compare two trusted-custody receipts, rejecting non-append-only drift.

    Digests detect inconsistency, not forgery or source authority. No implicit
    first checkpoint, accepted-data absence claim, or checkpoint write occurs.
    """
    before = _validated_members(previous)
    after = _validated_members(current)
    if previous["scope"] != current["scope"]:
        raise ValueError("MEMBERSHIP_SCOPE_MISMATCH")
    if before.keys() - after.keys():
        raise ValueError("MEMBERSHIP_REMOVED")
    if any(after[sha] != digest for sha, digest in before.items()):
        raise ValueError("LEDGER_IDENTITY_CHANGED")
    added = sorted(after.keys() - before.keys())
    return {
        "schema": "theseus.session-search-accepted-membership-delta.v1",
        "scope": current["scope"],
        "status": "ADDED" if added else "UNCHANGED",
        "currentness": CURRENTNESS,
        "previous_snapshot_sha256": previous["snapshot_sha256"],
        "current_snapshot_sha256": current["snapshot_sha256"],
        "added_artifacts": added,
    }
