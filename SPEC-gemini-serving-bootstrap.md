# Spec: gemini-serving-bootstrap

Status: DRAFT FOR HUMAN REVIEW
Capability map: CAPABILITY_MAP-gemini.md
Tracking issue: #311
Depends on: gemini-provider-allocation, gemini-currentness

## Objective

Add the first allocator-produced Gemini shard to the existing federated Session Search serving plane without changing the production App/router identity or weakening rollback, auth, telemetry or provenance.

This is a first-provider bootstrap, not a routine replacement shard.

## Tech Stack

- deterministic shard bundle contract
- scripts/verify_shard_source.py and scripts/shard_bundle.py
- Neon green DATA project lifecycle BUILDING -> READY
- existing reviewed shard Function source/auth lineage
- existing production federated router Function
- existing Arcade worker and Session Search App
- tools/dev/serving-promotion-readiness

Normative mechanics remain in docs/federated-serving-promotion-runbook.md and
docs/shard-bundle-transport.md; this module composes those contracts rather
than redefining their output schemas.

## Commands

Verify accepted source for the allocator-produced shard:

    python3 scripts/verify_shard_source.py --corpus /private/path/session-search-corpus --provider gemini --shard-id <allocator-shard-id>

Build deterministic shard bundle:

    python3 scripts/shard_bundle.py build --corpus /private/path/session-search-corpus --provider gemini --shard-id <allocator-shard-id> --output /private/path/gemini-shard.tar

Verify a built bundle:

    python3 scripts/shard_bundle.py verify --bundle /private/path/gemini-shard.tar

Run bounded local readiness:

    tools/dev/serving-promotion-readiness --manifest /private/path/gemini-promotion-readiness.json --run-qa --output /private/path/gemini-promotion-readiness-receipt.json

Canonical repository QA:

    ./tools/dev/check

## Project Structure

- scripts/verify_shard_source.py — accepted-source gate
- session_search/shard_bundle.py and scripts/shard_bundle.py — deterministic transport
- session_search/neon_materializer.py — data-generation materialization
- .github/workflows/materialize-session-search-building.yml — canonical BUILDING path
- .github/workflows/promote-session-search-ready.yml — canonical READY path
- functions/shard/ — reviewed serving shard Function
- functions/router/ — existing federated router
- docs/federated-serving-promotion-runbook.md — generic serving mechanics

## Code Style

Keep authority layers explicit:

    accepted corpus -> verified bundle -> READY data generation
    -> candidate shard -> candidate router -> production same-URI reload
    -> Arcade refresh -> App-backed acceptance

A successful earlier layer never implies success of the next layer.

Generation identity follows the existing materializer contract:

    generation_id = sha256("session-search-generation-v1\n" + source_manifest_sha256)

For a REPLACEMENT, previous_generation_id is the production generation
previously bound to the same shard_id. For the first allocator-produced Gemini
shard, promotion mode is NEW_SHARD and previous_generation_id is null.
Neither value is inferred from provider name or wall-clock order.

## Testing Strategy

- deterministic bundle build twice with identical bytes/hash;
- positive and negative authenticated candidate shard smoke;
- unauthenticated shard search returns 401;
- candidate router broad/provider-scoped controls with unavailable_shards=[];
- exact Gemini generation provenance;
- post-cutover same-URI direct router readback;
- existing Arcade worker refresh;
- fresh App-backed Gemini and legacy-provider acceptance;
- rollback rehearsal/readiness evidence retained;
- canonical ./tools/dev/check before mutation.

## Boundaries

Always:
- use allocator-produced shard identity;
- model the initial Gemini target as NEW_SHARD with null previous generation;
- keep the pre-Gemini catalog as rollback authority until terminal acceptance;
- preserve production router URI/identity, Arcade worker identity and App/plugin identity;
- force fresh runtime deployment after production catalog change;
- independently verify every consequential state transition.

Ask first:
- replacement App/router identity;
- shared auth-secret rotation;
- new Function-host architecture rather than inherited reviewed code/auth;
- any production release outside the #311 scope.

Never:
- deploy directly from an unverified corpus;
- invent a previous Gemini generation;
- expose DB URLs, bearer values, Drive IDs or transcript text in public receipts;
- claim success from deployment self-report without target readback.

## Success Criteria

- allocator-produced Gemini shard source integrity is VERIFIED;
- deterministic bundle bytes/hash are independently read back;
- fresh Neon Gemini generation reaches READY with expected identity/counts;
- candidate shard passes auth positive/negative controls;
- isolated router candidate includes existing catalog plus Gemini with zero unavailable;
- production catalog change uses the existing router identity/URI;
- same-URI runtime reload is observed;
- existing Arcade worker refresh succeeds;
- fresh App-backed Gemini coverage/search returns exact generation provenance and accepted currentness semantics;
- existing ChatGPT/DeepSeek/xAI controls remain healthy;
- pre-Gemini catalog rollback remains available through final disposition.

## Open Questions

- Transitional Function-host choice must be selected from the current accepted
  live serving lineage at execution time using the production router/catalog
  readback and the canonical promotion runbook. Cached project IDs, remembered
  endpoints, or a historical Issue are not specification authority.
