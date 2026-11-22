# Spec: gemini-currentness

Status: DRAFT FOR HUMAN REVIEW
Capability map: CAPABILITY_MAP-gemini.md
Tracking issue: #311
Evidence refinement: PR #319 content is incorporated here.

## Objective

Represent the strongest honest currentness claim supported by the accepted Google Takeout export without pretending that Takeout contains every Gemini conversation ever created.

The serving contract must distinguish a bounded snapshot of Gemini Apps Activity from account-complete history.

## Tech Stack

- official Google Takeout / Gemini Apps Activity documentation as source semantics
- exact Google provider notification receipts for the accepted export
- session_search serving currentness metadata
- bounded readiness and router/App coverage contracts
- Python and Function tests for classification/serialization behavior

## Commands

Inspect the exact official export:

    python3 -m session_search.google_gemini_activity takeout.tgz --inspect

Canonical repository QA:

    ./tools/dev/check

Future implementation tests must use the repository-focused currentness/router test commands discovered from the exact implementation diff before modification.

## Project Structure

- SPEC-gemini-currentness.md — authority for this capability
- session_search serving/readiness modules — local currentness metadata
- functions/router/ — federated coverage/currentness response
- tests/ and functions/tests/ — contract tests
- docs/gemini-official-corpus-serving-spec.md — supporting architecture narrative

## Code Style

Use scoped evidence vocabulary.

Target semantic shape:

    source_currentness = BOUNDED_PROVIDER_EXPORT_SNAPSHOT
    source_scope = GEMINI_APPS_ACTIVITY
    observed_through = 2026-11-21T15:45:28Z
    basis = provider_takeout_request_receipt

Never collapse that state into PROVIDER_REPORTED_NO_KNOWN_GAPS.

## Testing Strategy

- prove the accepted export request boundary from independent Google receipt evidence;
- prove parsed activity does not exceed the request boundary;
- test currentness serialization through readiness/router/App layers;
- negative tests must preserve explicit exclusions and reject stronger completeness labels;
- existing ChatGPT provider-watermark semantics must remain unchanged;
- canonical ./tools/dev/check plus runtime readback before production use.

## Boundaries

Always:
- scope the claim to Gemini Apps Activity;
- use Takeout request time, not archive-ready time, as the conservative upper boundary;
- preserve known Google exclusions in documentation/metadata;
- distinguish source currentness from index/shard finite bounds.

Ask first:
- any new label claiming provider-complete history;
- any attempt to reinterpret deleted or History-off data as covered;
- any source-semantic change based on undocumented filename conventions alone.

Never:
- claim all Gemini conversations ever created are present;
- treat archive-ready timestamp as a safe watermark when Google warns about request-to-creation changes;
- map this evidence to PROVIDER_REPORTED_NO_KNOWN_GAPS;
- hide Temporary Chat, History-off or deleted-history exclusions.

## Success Criteria

For the accepted export, evidence records:

- Takeout request receipt: 2026-11-21T15:45:28Z;
- archive-ready receipt: 2026-11-21T15:47:44Z;
- archive filename request timestamp matches the provider request receipt;
- 2,106 parsed prompt activities;
- earliest prompt activity 2025-03-22T03:41:03Z;
- latest prompt activity 2026-11-21T15:28:40Z;
- zero parsed prompt activities later than the request boundary.

Runtime support then returns BOUNDED_PROVIDER_EXPORT_SNAPSHOT for provider=gemini while preserving existing provider semantics elsewhere.

## Open Questions

- The exact external/public wording of exclusions in App UI may remain minimal as long as machine metadata preserves scope and currentness unambiguously.
