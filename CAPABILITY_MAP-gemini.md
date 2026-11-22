# Capability Map: Official Gemini corpus and federated serving

Status: APPROVED 2026-11-22
Tracking issue: #311

This map is the canonical Spec D index for the Gemini initiative. Existing implementation that predates this map must be reconciled against the module specs before further behavioral work.

| Module id | Responsibility | Depends on |
|---|---|---|
| gemini-source-acceptance | Turn official Google My Activity Gemini Apps export plus preserved NotebookLM rescue evidence into one verified durable accepted corpus without destructive reconciliation. | — |
| gemini-provider-allocation | Map accepted Gemini source adapters to provider=gemini and derive deterministic provider-aware shard membership and size. | gemini-source-acceptance |
| gemini-currentness | Define and verify the strongest honest time/currentness claim supported by Google Takeout documentation plus exact provider receipts for the accepted export. | gemini-source-acceptance |
| gemini-serving-bootstrap | Bootstrap the first Gemini shard into existing federated serving while preserving router/App identity, rollback, auth and independent runtime acceptance. | gemini-provider-allocation, gemini-currentness |

Build order:

    gemini-source-acceptance
        -> gemini-provider-allocation
        -> gemini-serving-bootstrap

    gemini-source-acceptance
        -> gemini-currentness
        -> gemini-serving-bootstrap

Provider allocation and currentness may be verified independently after source acceptance. Serving bootstrap starts only when both are accepted.

Canonical module specs:

- SPEC-gemini-source-acceptance.md
- SPEC-gemini-provider-allocation.md
- SPEC-gemini-currentness.md
- SPEC-gemini-serving-bootstrap.md

## Approval semantics

APPROVED on this capability map means the module boundaries, dependency
direction, and build order were human-approved. It does not approve the module
specifications automatically.

Each SPEC-gemini-*.md remains DRAFT FOR HUMAN REVIEW until separately
approved. Implementation of a module starts only after that module spec is
approved. gemini-serving-bootstrap additionally requires verified completion evidence
from all three upstream capabilities: the source-acceptance recovery receipt,
the allocator public summary plus VERIFIED shard-source integrity, and the
accepted currentness source_snapshot binding plus router-focused tests. A
locally green draft is not sufficient.

The older docs/gemini-official-corpus-serving-spec.md is supporting architecture narrative, not the Spec D authority index.
