# Spec: gemini-currentness

Status: APPROVED 2026-11-22
Capability map: CAPABILITY_MAP-gemini.md
Depends on: CAPABILITY_MAP-gemini.md (APPROVED); gemini-source-acceptance (verified acceptance receipt with accepted_set_sha256)
Tracking issue: #311

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

Focused router/currentness tests:

    cd functions && node --test tests/serving.test.mjs tests/semantic-events.test.mjs tests/router-pinned-stage2.test.mjs

Canonical repository QA:

    ./tools/dev/check

## Project Structure

- SPEC-gemini-currentness.md — authority for this capability
- session_search serving/readiness modules — local currentness metadata
- functions/router/ — federated coverage/currentness response
- tests/ and functions/tests/ — contract tests
- docs/gemini-official-corpus-serving-spec.md — supporting architecture narrative

## Code Style

Use scoped evidence vocabulary.

Target semantic shape:

    source_scope = GEMINI_APPS_ACTIVITY
    source_snapshot.observed_through = T_request
    source_snapshot.basis = provider_takeout_request_receipt

For provider-scoped coverage queries, source_currentness is query-relative:

    to_time <= T_request
        -> VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT

    to_time is absent, or to_time > T_request
        -> STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT

T_request is the independently observed Google Takeout request receipt time for
the accepted export. It is data, not a constant in the capability.

Never collapse either state into PROVIDER_REPORTED_NO_KNOWN_GAPS or the
provider-watermark states used by ChatGPT.

## Persistence Contract

The bounded Gemini export boundary is durable accepted-source evidence, not a
chat-local observation. gemini-source-acceptance owns persistence of the
provider-scoped source_snapshot in the accepted recovery receipt. This module
does not mutate that upstream recovery authority after acceptance.

The currentness implementation consumes the already recovered snapshot and
requires:

    provider = gemini
    source_scope = GEMINI_APPS_ACTIVITY
    observed_through = T_request
    basis = provider_takeout_request_receipt
    source_snapshot.accepted_set_sha256 = recovery_receipt.accepted_set_sha256

source_snapshot is deliberately distinct from source_watermark. The existing
source_watermark contract carries the stronger provider-reported-no-known-gaps
meaning and must not be overloaded for Takeout snapshot semantics.

Before any runtime classification is emitted, recovery validation must have
verified the snapshot binding. Provider-specific shard/promotion metadata may
copy the accepted Gemini snapshot for transport to the router, but it cannot
strengthen or reinterpret the evidence. The production router computes
user-visible currentness from that validated metadata.

## Runtime Ownership

The pre-cutover readiness layer
(session_search/serving_promotion_readiness.py) validates that promotion
metadata is internally consistent and may report the provider currentness
mode. It does not own the user-visible coverage verdict.

The federated router Function owns provider-scoped user-visible
source_currentness summarization. Its current implementation location is
functions/router/core.mjs. The router must emit the bounded Gemini
classification only from validated Gemini export metadata; the App reflects
that router result. Semantic-event schemas must accept the new value only after
router behavior and tests are updated.

This keeps one runtime owner for the coverage classification and prevents
shard, readiness, and App layers from inventing competing meanings.

## Testing Strategy

- prove the accepted export request boundary from independent Google receipt evidence;
- prove parsed activity does not exceed the request boundary;
- test currentness serialization through readiness/router/App layers, including one query bounded at or before T_request and one query extending past T_request;
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

For each accepted official Gemini export:

- the verified gemini-source-acceptance recovery receipt contains the Gemini
  source_snapshot bound to its accepted_set_sha256;
- an independent Google provider receipt establishes T_request;
- archive-ready time T_ready is recorded as delivery evidence but is not used
  as the currentness upper bound;
- if an archive filename embeds a timestamp, it is supporting evidence only and
  must not be authoritative unless it independently matches provider evidence;
- parsing yields a finite activity range [T_earliest, T_latest];
- zero parsed Gemini Apps Activity prompts have timestamps later than T_request;
- the accepted export identity, activity count/range, and request/archive
  receipts are recorded in an acceptance receipt or covering Issue evidence,
  not hard-coded into this reusable module specification;
- provider-scoped runtime coverage bounded at or before T_request returns
  VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT with
  source_scope=GEMINI_APPS_ACTIVITY and observed_through=T_request;
- provider-scoped runtime coverage with no upper bound or to_time > T_request
  returns STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT with the same snapshot
  metadata;
- existing ChatGPT provider-watermark semantics remain unchanged.

The currently accepted export's concrete timestamps/counts belong to the #311
acceptance evidence and supporting architecture narrative, not to this
capability's reusable acceptance constants.

## Open Questions

- The exact external/public wording of exclusions in App UI may remain minimal as long as machine metadata preserves scope and currentness unambiguously.
