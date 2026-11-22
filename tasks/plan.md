# Implementation Plan: Official Gemini corpus and federated serving

Status: DRAFT FOR HUMAN REVIEW
Tracking issue: #311
Capability map: CAPABILITY_MAP-gemini.md
Approved specs:
- SPEC-gemini-source-acceptance.md
- SPEC-gemini-provider-allocation.md
- SPEC-gemini-currentness.md
- SPEC-gemini-serving-bootstrap.md

## Overview

Complete the official Gemini ingestion and first federated serving bootstrap without
creating a second authority plane. The accepted corpus and recovery receipt remain
the source authority; allocation remains provider-aware-v1; currentness is derived
from accepted source_snapshot evidence; serving reuses the existing reviewed shard,
router, Arcade worker, and App identities.

The work is intentionally staged so every consequential mutation has an observable
readback before the next layer starts.

## Task List Target

Implementation tasks will be tracked as narrow GitHub Issues after this plan is
approved. This file remains the ordered dependency/architecture plan; it will not
duplicate issue checklists.

## Dependency Graph

    P1 recovery source_snapshot contract
        |
        v
    P2 official Gemini source acceptance
        | \
        |  \
        v   v
    P3 allocation verification      P4 currentness runtime
        |
        v
    P5 Gemini data plane READY
        \                           /
         \                         /
          +-----------+-----------+
                      |
                      v
              P6 candidate serving
                      |
                      v
              P7 production same-URI cutover

P3 and P4 may proceed in parallel only after P2 has a VERIFIED recovery receipt
bound to the accepted_set_sha256. P5 depends on P3 but not P4; P4 and P5 join
only at P6.

## Architecture Decisions

1. **Extend the existing recovery receipt; do not create another authority.**
   Add optional source_snapshot evidence to
   theseus.session-search-recovery-receipt.v1. Existing receipts without the field
   remain valid. Nested accepted_set_sha256 must equal the receipt top-level value.

2. **Source acceptance owns source evidence.**
   gemini-source-acceptance persists the verified Takeout request boundary before
   the current recovery pointer moves. gemini-currentness is a downstream consumer
   and must not mutate accepted recovery authority.

3. **Reuse provider-aware-v1 allocation.**
   google-gemini-myactivity and notebooklm-preserved-gemini map to provider=gemini.
   Unknown-only provenance remains unresolved; one known provider plus unknown
   provenance retains the known provider; conflicting known providers block.
   Shard count and IDs come only from allocator output.

4. **Currentness is query-relative and router-owned.**
   The accepted snapshot carries source_scope=GEMINI_APPS_ACTIVITY,
   observed_through=T_request, and basis=provider_takeout_request_receipt.
   Machine metadata normalizes observed_through to finite Unix seconds so it
   composes with existing router time bounds; human evidence may additionally
   retain the equivalent RFC3339 timestamp.
   Provider-scoped coverage at/before T_request returns
   VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT. Unbounded coverage or a window
   extending past T_request returns STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT.
   The federated router owns the user-visible classification; readiness validates
   transport/binding but does not invent a second semantic interpretation.

5. **First Gemini serving target is NEW_SHARD.**
   The initial allocator-produced Gemini shard has previous_generation_id=null.
   A collision with an existing live shard ID blocks promotion.

6. **Serving identity remains stable.**
   Build a fresh Gemini DATA generation, then use the existing reviewed Function
   code/auth lineage, candidate router, same production router URI/identity,
   existing Arcade worker, and existing Session Search App. Do not replace these
   identities merely to add Gemini.

7. **No layer implies the next layer succeeded.**
   Accepted corpus, verified bundle, READY data generation, candidate shard,
   candidate router, production reload, Arcade refresh, and App acceptance are
   separately observed states.

## Phase P1 — Recovery source_snapshot contract

Goal: make the existing recovery authority capable of carrying bounded provider
snapshot evidence without changing the receipt schema identifier.

Work:
- add optional source_snapshot validation/build support;
- validate provider, source_scope, observed_through, basis, and SHA binding;
- require source_snapshot.accepted_set_sha256 to equal the receipt top-level digest;
- preserve backward compatibility for existing v1 receipts;
- verify serialization/recovery readback and reject malformed/strengthened evidence.

Checkpoint P1:
- focused recovery tests PASS;
- old receipts remain valid;
- invalid/mismatched snapshot bindings fail closed;
- canonical ./tools/dev/check PASS;
- exact-head review CLEAN.

## Phase P2 — Official Gemini source acceptance

Goal: produce a VERIFIED accepted corpus and durable recovery generation from the
current accepted baseline plus the official Gemini Takeout, while preserving the
NotebookLM rescue lane.

Work:
- recover the current accepted baseline from the authoritative current recovery
  pointer at execution time;
- re-identify the exact official Takeout by its recorded hash;
- inspect/materialize official Gemini artifacts with the merged adapter;
- build a separate candidate from accepted evidence only;
- preserve rescue artifacts and record exact-overlap reconciliation aggregates;
- attach the independently verified Gemini Apps Activity source_snapshot to the
  recovery receipt;
- verify full projection, publish immutable recovery bytes, update the current
  pointer only after verification, and perform fresh-runtime recovery readback.

Checkpoint P2:
- recovery receipt status VERIFIED;
- accepted_set_sha256 recorded;
- source_snapshot bound to that same accepted_set_sha256;
- rescue lane preserved;
- immutable bytes/readback/current pointer verified;
- no private transcript text or secret locator is published.

## Phase P3 — Provider allocation verification

Goal: prove the accepted Gemini corpus fits the existing provider-aware-v1
allocator contract without inventing allocation policy.

Work:
- run the canonical allocator on the P2 accepted corpus;
- record the public-safe Gemini shard plan;
- verify mappings for both accepted Gemini adapters;
- run negative/conflict and mixed known-plus-unknown provenance tests;
- verify every planned Gemini shard against accepted-source integrity.

Checkpoint P3:
- allocator output is deterministic;
- shard count/IDs/estimated bytes come from allocator output;
- unresolved/conflicting provenance remains fail-closed;
- every planned Gemini shard reports status=VERIFIED from the canonical
  shard-integrity verifier.

If no code change is required, P3 closes as verification-only evidence rather than
manufacturing a code PR.

## Phase P4 — Bounded snapshot currentness runtime

Goal: carry accepted Gemini source_snapshot evidence through readiness and serving
without weakening existing ChatGPT watermark semantics.

Work:
- extend promotion/readiness metadata to transport validated source_snapshot;
- keep source_watermark semantics unchanged;
- extend federated router normalization and coverage summarization for the two
  bounded-snapshot currentness states;
- update semantic-event/schema acceptance for the new values;
- add focused positive/negative tests at the request boundary;
- verify App-facing coverage/search serialization.

Checkpoint P4:
- query to_time <= T_request -> VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT;
- missing to_time or to_time > T_request ->
  STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT;
- source_scope and observed_through survive serialization;
- ChatGPT provider-watermark tests remain unchanged and green;
- canonical ./tools/dev/check PASS;
- exact-head review CLEAN.

## Phase P5 — Gemini data plane READY

Goal: materialize the allocator-produced Gemini shard(s) into verified immutable
serving generations.

Work:
- build deterministic bundle(s) twice and compare bytes/hash;
- verify bundle source against the P2 accepted corpus;
- create fresh Gemini Neon DATA project/generation in BUILDING state;
- materialize using the canonical workflow;
- independently read back generation identity, counts, bounds, and state;
- promote to READY only through the canonical READY path;
- produce bounded serving-promotion-readiness receipt using NEW_SHARD semantics.

Checkpoint P5:
- deterministic bundle hash verified;
- accepted source integrity VERIFIED;
- generation_id/source_manifest/bundle hashes consistent;
- DATA generation READY by independent readback;
- readiness receipt READY_FOR_RUNTIME_GATES with previous_generation_id=null;
- no live router/App mutation yet.

## Phase P6 — Candidate serving

Goal: prove allocator-produced Gemini shard(s) and the expanded federated router
fan-out before production identity is touched.

Work:
- bind the READY Gemini data generation to reviewed shard Function code/auth;
- run authenticated positive and negative candidate-shard controls;
- construct an isolated router candidate containing the existing live catalog plus
  Gemini;
- test broad and provider-scoped searches/coverage;
- exercise both Gemini bounded-snapshot currentness sides;
- verify unavailable_shards=[] and exact generation provenance;
- retain the pre-Gemini production catalog as rollback authority.

Checkpoint P6:
- candidate shard auth positive/negative controls PASS;
- candidate router has zero unavailable shards;
- Gemini positive search and coverage PASS;
- existing ChatGPT/DeepSeek/xAI controls remain healthy;
- rollback catalog captured and independently readable.

## Phase P7 — Production same-URI cutover

Goal: add Gemini to production without changing the public router/App identity.

Work:
- verify exact current production router identity/URI and catalog immediately
  before mutation;
- update the production catalog to the P6 accepted catalog;
- force a fresh runtime deployment on the same router URI so module-scope catalog
  state reloads;
- independently read back direct router coverage/search;
- refresh the existing Arcade worker;
- run fresh App-backed Gemini and legacy-provider acceptance;
- retain rollback until terminal acceptance;
- record terminal disposition in #311.

Checkpoint P7:
- same production router URI/identity observed after reload;
- App/worker identity unchanged;
- Gemini App-backed search/coverage returns exact generation and accepted
  currentness semantics;
- legacy providers remain healthy;
- unavailable_shards=[];
- rollback remains available through terminal acceptance;
- final disposition is LIVE_SERVING_VERIFIED or an explicit non-success state.

## Verification Strategy

Every implementation issue must use the smallest deterministic verification route
already present in the repository before inventing bespoke checks.

Standing gates:
- TDD for behavioral changes;
- ./tools/dev/check before promotion;
- exact-head review gate for protected changes;
- public-safe receipts only;
- independent readback for consequential runtime writes;
- current main/base/head verification before routine promotion.

Runtime verification is layered:

    accepted evidence
      -> projection verification
      -> recovery readback
      -> allocation/source integrity
      -> bundle verification
      -> READY data readback
      -> candidate shard
      -> candidate router
      -> production same-URI reload
      -> Arcade refresh
      -> App acceptance

Failure at one layer stops forward promotion; it does not erase already verified
upstream evidence.

## Planned Issue Sizing

After plan approval, create one narrow GitHub Issue per phase P1-P7. If a phase
cannot fit in one focused implementation session or would touch more than roughly
five files, split that phase vertically while preserving the checkpoint as the
parent acceptance boundary.

P3 is expected to remain verification-only unless current evidence demonstrates an
actual contract mismatch.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Accepted baseline changes before P2 | Candidate built from stale authority | Recover current pointer at execution time; never use old working-directory state as authority |
| source_snapshot accidentally becomes a second watermark | Overstated completeness | Separate field/vocabulary; preserve source_watermark semantics and tests |
| Recovery receipt extension breaks old generations | Recovery regression | Optional backward-compatible v1 field plus old-receipt regression tests |
| Gemini shard sizing differs from expectation | Wrong hard-coded topology | Allocator owns shard count/IDs; no assumed gemini-0001 in planning |
| Router catalog updated without runtime reload | Production serves stale module-scope catalog | Fresh deployment on same URI plus direct same-URI readback |
| New provider breaks legacy fan-out | Cross-provider regression | Candidate and terminal legacy-provider controls with unavailable_shards=[] |
| Private Takeout/Drive evidence leaks into GitHub | Privacy/security incident | Public-safe aggregates/hashes only; private locators and transcript text stay outside public receipts |
| Provider/model review route unavailable | Review stalls | Codex-first gate with already-merged fail-closed alternative exact-head review fallback |
| Cached runtime identifiers drift | Mutation targets wrong resource | Re-read current live router/data identities immediately before runtime mutation |

## Parallelization

After P2:
- P3 allocation verification and P4 currentness runtime may run independently.
- P5 may start as soon as P3 is verified; it does not wait for P4.
- P6 waits for both P4 currentness and P5 READY data-plane evidence.
- P6 and P7 are sequential because they mutate/read the same serving lineage.

No production mutation is parallelized with another serving mutation.

## Open Questions

No question blocks PLAN approval.

Deferred execution-time choices:
- exact Gemini shard count/IDs come from the accepted-corpus allocator run;
- transitional Function host is selected from the current verified live lineage at
  P6/P7 time, not from cached IDs;
- App wording for source exclusions may remain minimal as long as machine metadata
  preserves scope/currentness unambiguously.

## Definition of Plan Completion

This plan is complete when:
- the human approves the dependency graph and architecture decisions;
- the plan is merged to main;
- P1-P7 are materialized as narrow GitHub Issues with acceptance/verification
  criteria and dependency links;
- no implementation begins before those task artifacts exist.
