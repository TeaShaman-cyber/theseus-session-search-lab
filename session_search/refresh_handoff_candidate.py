"""Private, conservative recovery handoff; never source authority or an ack."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .accepted_membership import CURRENTNESS, compare_accepted_membership
from .serving_promotion_readiness import RECEIPT_SCHEMA

SCHEMA = "theseus.session-search-recovery-handoff-candidate.v1"
DELTA_SCHEMA = "theseus.session-search-rederived-session-delta.v1"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _catalog_vector(rows: list[dict]) -> dict[str, tuple[str, str]]:
    vector = {}
    for row in rows:
        shard = row["shard_id"]
        if shard in vector or row["state"] != "READY":
            raise ValueError("CATALOG_INVALID")
        vector[shard] = (row["provider"], row["generation_id"])
    return vector


def prepare_recovery_handoff(
    *, provider: str, previous: dict, delta: dict, promotion: dict,
    catalog: list[dict], bundle_paths: dict[str, Path],
    verify_selection: Callable[..., tuple[dict, tuple[str, ...]]],
) -> dict:
    """Bind trusted source recovery to exact full-provider replacement.

    Caller supplies a provider-exclusive corpus scope, trusted rederived
    delta and readiness receipt, independent complete catalog readback,
    immutable bundle custody and the existing independent verifier.
    This is a private candidate, not an authorization or delivery proof.
    Partial provider replacement is deliberately unsupported.
    """
    previous, delta, promotion, catalog = copy.deepcopy((previous, delta, promotion, catalog))
    current = delta["current_membership"]
    compare_accepted_membership(previous, current)
    if (
        delta.get("schema") != DELTA_SCHEMA
        or delta.get("status") != "REDERIVED"
        or delta.get("scope") != previous["scope"]
        or delta.get("baseline_verification") != "rederived_under_current_contract"
        or delta.get("current_verification") != "full_derivation"
        or delta.get("currentness") != CURRENTNESS
        or delta.get("previous_snapshot_sha256") != previous["snapshot_sha256"]
        or delta.get("previous_accepted_set_sha256") != previous["accepted_set_sha256"]
        or delta.get("current_accepted_set_sha256") != current["accepted_set_sha256"]
    ):
        raise ValueError("DELTA_BINDING_MISMATCH")
    groups = []
    for key in ("added", "changed", "removed"):
        ids = delta.get(key)
        if (not isinstance(ids, list) or any(not isinstance(s, str) or not s.strip() for s in ids)
                or ids != sorted(set(ids))):
            raise ValueError("DELTA_SESSION_IDS_INVALID")
        groups.append(set(ids))
    added, changed, removed = groups
    if added & changed or added & removed or changed & removed:
        raise ValueError("DELTA_SESSION_IDS_OVERLAP")
    if (promotion.get("schema") != RECEIPT_SCHEMA
            or promotion.get("state") != "READY_FOR_RUNTIME_GATES"
            or promotion.get("blockers") != []):
        raise ValueError("PROMOTION_NOT_READY")
    replacements = promotion["replacement_shards"]
    unchanged = promotion["unchanged_catalog"]
    if not replacements or any(row["provider"] != provider for row in replacements) or any(
        row["provider"] == provider for row in unchanged
    ):
        raise ValueError("FULL_PROVIDER_REPLACEMENT_REQUIRED")
    expected = _catalog_vector(replacements + unchanged)
    if expected != _catalog_vector(catalog):
        raise ValueError("CATALOG_MISMATCH")
    paths = dict(bundle_paths)
    if set(paths) != {row["shard_id"] for row in replacements}:
        raise ValueError("BUNDLE_PATH_SET_MISMATCH")

    selected = {}
    wanted = sorted(added | changed | removed)
    for row in replacements:
        shard = row["shard_id"]
        if row["accepted_set_sha256"] != current["accepted_set_sha256"]:
            raise ValueError("BUNDLE_ACCEPTED_SET_MISMATCH")
        receipt, matches = verify_selection(
            paths[shard], expected_bundle_sha256=row["bundle_sha256"], session_ids=wanted,
        )
        if receipt.get("status") != "VERIFIED" or any(
            receipt.get(key) != row[key] for key in (
                "provider", "shard_id", "generation_id", "source_manifest_sha256",
                "bundle_sha256", "accepted_set_sha256", "session_count", "message_count",
            )
        ):
            raise ValueError("BUNDLE_PROMOTION_MISMATCH")
        for sid in matches:
            if sid in removed or sid not in added | changed or sid in selected:
                raise ValueError("PLACEMENT_CONFLICT")
            selected[sid] = {
                "session_id": sid, "shard_id": shard, "generation_id": row["generation_id"],
                "source_manifest_sha256": row["source_manifest_sha256"],
                "bundle_sha256": row["bundle_sha256"], "change": "ADDED" if sid in added else "CHANGED",
            }
    if set(selected) != added | changed:
        raise ValueError("PLACEMENT_MISSING")
    core = {
        "schema": SCHEMA, "status": "HANDOFF_CANDIDATE", "provider": provider,
        "scope": previous["scope"], "currentness": CURRENTNESS,
        "delta_contract": DELTA_SCHEMA, "baseline_verification": delta["baseline_verification"],
        "previous_membership": previous, "current_membership": current,
        "promotion_receipt_sha256": _digest(promotion),
        "catalog": [{"shard_id": shard, "provider": p, "generation_id": g}
                    for shard, (p, g) in sorted(expected.items())],
        "selected": [selected[sid] for sid in sorted(selected)],
        "removed": sorted(removed), "checkpoint_advanced": False,
    }
    return {**core, "candidate_sha256": _digest(core)}
