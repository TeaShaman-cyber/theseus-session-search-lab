from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_sha256(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError("INVALID_ARTIFACT_SHA256")
    return value


def set_digest(values: Iterable[str]) -> str:
    checked = sorted({_validate_sha256(value) for value in values})
    digest = hashlib.sha256()
    for value in checked:
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def load_accepted_ledger(corpus_root: Path) -> set[str]:
    accepted_dir = Path(corpus_root) / "ledger" / "accepted"
    if not accepted_dir.is_dir():
        raise ValueError("ACCEPTED_LEDGER_MISSING")

    identities: set[str] = set()
    for path in sorted(accepted_dir.glob("*.json")):
        filename_sha = _validate_sha256(path.stem)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("INVALID_ACCEPTED_LEDGER_ENTRY") from exc
        payload_sha = _validate_sha256(payload.get("artifact_sha256"))
        if payload_sha != filename_sha:
            raise ValueError("LEDGER_IDENTITY_MISMATCH")
        if payload_sha in identities:
            raise ValueError("DUPLICATE_ARTIFACT_IDENTITY")
        identities.add(payload_sha)
    return identities


def load_frozen_provider_manifest(path: Path) -> dict[str, set[str]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("INVALID_FROZEN_MANIFEST") from exc

    providers = payload.get("providers")
    if not isinstance(providers, dict):
        raise ValueError("INVALID_FROZEN_MANIFEST")

    result: dict[str, set[str]] = {}
    owner_by_identity: dict[str, str] = {}
    for provider, metadata in providers.items():
        if not isinstance(provider, str) or not provider:
            raise ValueError("INVALID_PROVIDER_NAME")
        if not isinstance(metadata, dict):
            raise ValueError("INVALID_PROVIDER_MANIFEST")
        artifacts = metadata.get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError("INVALID_PROVIDER_MANIFEST")

        identities: set[str] = set()
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise ValueError("INVALID_PROVIDER_MANIFEST")
            sha = _validate_sha256(artifact.get("sha256"))
            if sha in identities:
                raise ValueError("DUPLICATE_ARTIFACT_IDENTITY")
            prior_provider = owner_by_identity.get(sha)
            if prior_provider is not None and prior_provider != provider:
                raise ValueError("ARTIFACT_PROVIDER_CONFLICT")
            owner_by_identity[sha] = provider
            identities.add(sha)
        result[provider] = identities
    return result


def classify_overlap(authority: set[str], candidate: set[str]) -> dict[str, Any]:
    authority_checked = {_validate_sha256(value) for value in authority}
    candidate_checked = {_validate_sha256(value) for value in candidate}
    overlap = authority_checked & candidate_checked
    novel = candidate_checked - authority_checked

    if not candidate_checked:
        status = "EMPTY"
    elif not overlap:
        status = "DISJOINT"
    elif not novel:
        status = "FULL_OVERLAP"
    else:
        status = "PARTIAL_OVERLAP"

    return {
        "status": status,
        "candidate_count": len(candidate_checked),
        "overlap_count": len(overlap),
        "novel_count": len(novel),
        "candidate_set_sha256": set_digest(candidate_checked),
        "overlap_set_sha256": set_digest(overlap),
        "novel_set_sha256": set_digest(novel),
    }


def audit_provider_sets(
    authority: set[str],
    provider_sets: Mapping[str, set[str]],
) -> dict[str, Any]:
    authority_checked = {_validate_sha256(value) for value in authority}
    providers = []
    for provider in sorted(provider_sets):
        result = classify_overlap(authority_checked, provider_sets[provider])
        providers.append({"provider": provider, **result})

    return {
        "schema": "theseus.session-search-artifact-overlap.v1",
        "authority_count": len(authority_checked),
        "authority_set_sha256": set_digest(authority_checked),
        "providers": providers,
    }
