# Spec: gemini-provider-allocation

Status: APPROVED 2026-11-22
Capability map: CAPABILITY_MAP-gemini.md
Depends on: CAPABILITY_MAP-gemini.md (APPROVED); gemini-source-acceptance (verified acceptance receipt)
Tracking issue: #311
Existing implementation lineage: #317 / merged PR #318

## Objective

Route accepted Gemini evidence into an explicit provider=gemini lineage and derive deterministic provider-aware shard membership from the accepted corpus.

This module decides allocation only. It does not accept new source evidence and does not deploy serving infrastructure.

## Tech Stack

- Python 3.11
- session_search/shard_allocation.py
- existing provider-aware allocation contract
- scripts/plan_neon_shards.py
- scripts/verify_shard_source.py
- canonical repository QA

## Commands

Plan provider-aware allocation from an accepted corpus using the checked-in planner:

    python3 scripts/plan_neon_shards.py --help

Verify an allocator-produced Gemini shard against accepted source authority:

    python3 scripts/verify_shard_source.py --corpus /private/path/session-search-corpus --provider gemini --shard-id <allocator-shard-id>

Canonical repository QA:

    ./tools/dev/check

The concrete shard ID in verification commands comes from allocator output; gemini-0001 is not assumed when planning.

## Project Structure

- session_search/shard_allocation.py — provider mapping and deterministic allocation
- scripts/plan_neon_shards.py — current canonical CLI for the allocator's
  public-safe planning surface; the allocator contract, not this path name, is
  authoritative
- session_search/shard_integrity.py — accepted-source replay/integrity
- scripts/verify_shard_source.py — bounded shard source verifier
- tests/test_shard_allocation.py and related tests — provider/allocation contract
- receipts/ — public-safe allocator evidence

## Code Style

Provider mapping follows the existing provider-aware-v1 contract:

    known adapters from exactly one provider -> that provider
    unknown-only adapters -> unresolved
    known adapters from more than one provider -> blocked conflict
    one known provider plus unknown adapters -> known provider, with all adapter provenance preserved

Normative mappings for this capability are:

- google-gemini-myactivity -> gemini;
- notebooklm-preserved-gemini -> gemini.

Do not infer provider from arbitrary filename or free text.

Shard identity is allocator-owned. Under the current provider-aware-v1
contract, _finalize_shard emits <provider>-<four-digit-number> (for example
gemini-0001). Specs and serving commands consume allocator output rather than
constructing an ID independently.

## Testing Strategy

- exact adapter-to-provider mapping tests for official Gemini My Activity and NotebookLM rescue adapters;
- negative tests for unknown-only Gemini-like adapter names and provider conflicts;
- mixed known-plus-unknown tests preserving provenance while retaining the single known provider;
- deterministic allocation repeatability;
- hard-limit/target-size contract checks;
- shard source replay verification against the accepted corpus;
- canonical ./tools/dev/check.

## Boundaries

Always:
- derive allocation from accepted corpus state;
- preserve source_adapter provenance even when multiple adapters share provider=gemini;
- let the canonical allocator determine shard count and IDs;
- fail closed on provider conflicts and unknown-only unresolved routing; preserve unknown co-routed provenance when exactly one known provider is present.

Ask first:
- allocation contract-version change;
- target/hard-limit changes that affect all providers;
- provider-family semantics broader than the accepted Gemini adapters.

Never:
- hard-code one shard because current Gemini data looks small;
- route AI Mode to provider=gemini without a separate accepted source contract;
- bypass source-integrity verification because a bundle is internally valid.

## Success Criteria

- both accepted Gemini adapters map deterministically to provider=gemini;
- unknown-only adapters remain unresolved, provider conflicts block, and mixed known-plus-unknown sessions retain the one known provider without dropping unknown provenance;
- current accepted corpus allocation produces an explainable Gemini shard plan;
- shard count, IDs, estimated bytes and bounds are recorded from allocator output;
- every planned Gemini shard passes scripts/verify_shard_source.py against the
  accepted corpus before bundle promotion; passing means exit 0 with
  status=VERIFIED from the canonical shard-integrity contract.

## Open Questions

- Exact shard count and ID remain UNKNOWN until the allocator runs against the finally accepted corpus.
