from __future__ import annotations

import dataclasses
from collections import defaultdict
from collections.abc import Iterable

from .artifact import NormalizedArtifact

ROUTING_VERSION = "chatgpt-branch-routing-v1"
OfficialFamilies = dict[str, dict[str, set[tuple[str, str]]]]


@dataclasses.dataclass(frozen=True)
class ProjectionRoute:
    accepted_session_id: str
    projected_session_id: str
    state: str
    reason: str


def _message_evidence(artifact: NormalizedArtifact) -> set[tuple[str, str]]:
    evidence: set[tuple[str, str]] = set()
    for message in artifact.messages:
        if message.message_id is None:
            continue
        evidence.add((message.message_id, message.canonical_message_sha256))
        if message.projection_source_canonical_sha256 is not None:
            evidence.add(
                (message.message_id, message.projection_source_canonical_sha256)
            )
    return evidence


def build_official_families(
    artifacts: Iterable[NormalizedArtifact],
) -> OfficialFamilies:
    materialized = [
        artifact
        for artifact in artifacts
        if artifact.source_adapter == "chatgpt-export"
    ]
    snapshot_expected: dict[tuple[str, str], int] = {}
    snapshot_branches: dict[tuple[str, str], set[str]] = defaultdict(set)
    source_has_branched_snapshot: dict[str, bool] = defaultdict(bool)
    families: dict[str, dict[str, set[tuple[str, str]]]] = defaultdict(
        lambda: defaultdict(set)
    )

    for artifact in materialized:
        if (
            artifact.source_export_sha256 is None
            or artifact.source_conversation_id is None
            or artifact.branch_count is None
            or artifact.branch_count < 1
        ):
            raise RuntimeError(
                "RECONCILIATION_REQUIRED: incomplete ChatGPT branch provenance"
            )
        snapshot_key = (
            artifact.source_export_sha256,
            artifact.source_conversation_id,
        )
        previous = snapshot_expected.get(snapshot_key)
        if previous is not None and previous != artifact.branch_count:
            raise RuntimeError(
                "RECONCILIATION_REQUIRED: inconsistent ChatGPT branch count"
            )
        snapshot_expected[snapshot_key] = artifact.branch_count
        snapshot_branches[snapshot_key].add(artifact.session_id)
        if artifact.branch_count > 1:
            source_has_branched_snapshot[artifact.source_conversation_id] = True

    for snapshot_key, expected_count in snapshot_expected.items():
        source_id = snapshot_key[1]
        branch_ids = snapshot_branches[snapshot_key]
        if len(branch_ids) != expected_count:
            raise RuntimeError(
                "RECONCILIATION_REQUIRED: incomplete ChatGPT branch family"
            )
        if source_id not in branch_ids:
            raise RuntimeError("RECONCILIATION_REQUIRED: ChatGPT base branch missing")

    for artifact in materialized:
        source_id = artifact.source_conversation_id
        assert source_id is not None
        assert artifact.branch_count is not None
        if source_has_branched_snapshot[source_id] and artifact.branch_count == 1:
            continue
        families[source_id][artifact.session_id].update(_message_evidence(artifact))
    return {source: dict(branches) for source, branches in families.items()}


def route_artifact(
    artifact: NormalizedArtifact,
    families: OfficialFamilies,
) -> ProjectionRoute:
    source_id = (
        artifact.source_conversation_id
        if artifact.source_adapter == "chatgpt-export"
        else artifact.session_id
    )
    branches = families.get(source_id)
    if not branches or len(branches) <= 1:
        return ProjectionRoute(
            accepted_session_id=artifact.session_id,
            projected_session_id=artifact.session_id,
            state="DIRECT",
            reason="explicit_session_identity",
        )
    if artifact.session_id in branches and artifact.session_id != source_id:
        return ProjectionRoute(
            accepted_session_id=artifact.session_id,
            projected_session_id=artifact.session_id,
            state="DIRECT",
            reason="explicit_branch_session_identity",
        )

    branch_ids = set(branches)
    candidates = set(branch_ids)
    discriminating = False
    for pair in _message_evidence(artifact):
        matching = {
            branch_id
            for branch_id, evidence in branches.items()
            if pair in evidence
        }
        if not matching or matching == branch_ids:
            continue
        discriminating = True
        candidates.intersection_update(matching)
        if not candidates:
            raise RuntimeError("FAILED_BRANCH_RECONCILIATION_CONFLICT")

    if discriminating and len(candidates) == 1:
        projected = next(iter(candidates))
        state = "BRANCH_MATCH"
        reason = "exact_branch_discriminating_overlap"
    else:
        projected = f"{source_id}~unresolved-{artifact.artifact_sha256[:12]}"
        state = "UNRESOLVED"
        reason = "ambiguous_or_nondiscriminating_overlap"
    return ProjectionRoute(
        accepted_session_id=artifact.session_id,
        projected_session_id=projected,
        state=state,
        reason=reason,
    )


def plan_projection_routes(
    artifacts: Iterable[NormalizedArtifact],
) -> dict[str, ProjectionRoute]:
    materialized = list(artifacts)
    families = build_official_families(materialized)
    return {
        artifact.artifact_sha256: route_artifact(artifact, families)
        for artifact in materialized
    }
