# Authority note

INSTANCE-SPECIFIC EVIDENCE NOTE: dated timestamps, counts, hashes, and request/archive
facts in this supporting document describe the initial #311 acceptance only. They
must not be copied into reusable Gemini module logic or treated as constants. The
root SPEC-gemini-*.md files define reusable behavior.

This file is supporting architecture narrative. Canonical Spec D authority for
the Gemini initiative is indexed by ../CAPABILITY_MAP-gemini.md and the four
root SPEC-gemini-*.md module specifications. If this narrative conflicts with a
module spec, the approved Spec D artifact wins.

# Official Gemini corpus acceptance and first federated serving promotion

## Status

Issue: #311
Prerequisite import line: #299 / PR #301
Current state: SPECIFIED / OFFICIAL ADAPTER LINE EXISTS / OFFICIAL CORPUS NOT YET ACCEPTED / GEMINI NOT YET LIVE

This document is the execution specification for moving the official Google My
Activity -> Gemini Apps export from portable evidence into the durable Session
Search corpus and, only after durable acceptance, into the existing federated
serving plane.

It intentionally separates three authority domains:

1. source/import authority;
2. accepted durable corpus/recovery authority;
3. derived federated serving authority.

Do not collapse them into one "Gemini deployed" claim.

## Architecture map

    official Google My Activity / Gemini Apps Takeout
      |
      v
    #299 / PR #301 official Gemini adapter
      |
      +--> portable official Gemini artifacts
      |
      +--> non-destructive reconciliation with accepted
           NotebookLM-preserved Gemini evidence
      |
      v
    accepted corpus candidate
      |
      +--> full artifact-derived projection rebuild / semantic verification
      |
      v
    immutable Drive recovery generation
      |
      +--> byte/readback receipt
      +--> current pointer readback
      +--> independent empty-runtime recovery
      |
      v
    explicit provider lineage: provider=gemini
      |
      +--> official Gemini adapter
      +--> NotebookLM-preserved Gemini adapter
      |
      v
    deterministic provider-aware shard allocation
      |
      v
    verified Gemini shard bundle
      |
      v
    fresh Neon Gemini DATA project
      BUILDING -> READY
      |
      v
    reviewed shard FUNCTION host
      inherited code/auth + least-privilege Gemini DB binding
      |
      v
    isolated router candidate
      existing production catalog + Gemini
      |
      v
    same production router identity/URI
      catalog add -> runtime reload
      |
      v
    existing Arcade worker refresh
      |
      v
    existing Session Search App
      Gemini coverage/search provenance readback

For generic mechanics, follow
[federated serving promotion runbook](federated-serving-promotion-runbook.md).
This specification adds first-provider/new-shard and Gemini-specific invariants.

## Source authority

### Official My Activity lane

PR #301 currently defines the official provider-origin adapter contract:

- only the Gemini Apps product surface is parsed;
- AI Mode remains a separate product/source lane;
- one explicit Gemini app ID creates provider conversation identity;
- missing or multiple app IDs remain deterministic unresolved evidence;
- prompt activity timestamps are preserved;
- independent assistant timestamps are not invented;
- attachment references are provenance, not implicit attachment content;
- parent Takeout/member digests bind child artifacts;
- coverage stays PARTIAL_SESSION_SLICE.

The observed private dry-run on the official export produced:

- 3,691 activity cards;
- 2,106 prompt cards;
- 4,205 messages;
- 65 portable child artifacts.

These are dated dry-run facts. Re-run after rebasing/merging the final adapter;
do not assume those counts if source bytes or adapter head changes.

### NotebookLM rescue lane

The already accepted NotebookLM-preserved Gemini evidence remains secondary
preservation. It must not be rewritten or deleted merely because official My
Activity evidence exists.

Both lanes may eventually route to provider=gemini for serving while retaining
their distinct source_adapter and provenance records.

Exact-text grouping may merge equal visible bodies in retrieval while preserving
all historical occurrences/provenance. That is serving presentation, not ledger
deduplication.

## Phase A — finish official adapter

Canonical implementation line is #299 / PR #301.

Before merge:

1. rebase/update PR #301 against current main;
2. preserve or improve every current adapter invariant;
3. inspect every existing Codex finding and prove it fixed or explicitly
   dispositioned on the exact final head;
4. run focused Gemini tests;
5. run ./tools/dev/check;
6. require hosted CI/runtime gates;
7. merge only when exact head/base are current and no blocker remains.

PR #300 / issue #298 are older parallel work. Once #301 is verified canonical,
mark the older line superseded without discarding its historical evidence.

No accepted corpus or serving state changes in Phase A.

## Phase B — durable official corpus acceptance

Start from the current accepted recovery baseline, which already contains the
NotebookLM rescue lane and the latest accepted ChatGPT/DeepSeek/xAI evidence.
Do not build from the old pre-refresh candidate used during #296.

1. independently recover the current accepted baseline into a clean workdir;
2. materialize official Gemini artifacts with merged adapter main;
3. run aggregate-only reconciliation against accepted NotebookLM evidence;
4. ingest all official artifacts without deleting rescue artifacts;
5. rebuild the full artifact-derived projection;
6. require semantic verification = VERIFIED;
7. record accepted-artifact/session/message/searchable counts and official vs
   rescue overlap/new-only evidence;
8. build deterministic recovery projection + accepted-evidence package;
9. publish immutable Drive generation only after verification;
10. independently read back uploaded bytes/receipt/current pointer;
11. recover again into a separate empty runtime and require VERIFIED.

Only after step 11 is the official Gemini corpus durably accepted.

No live Neon/router change belongs in Phase B.

## Phase C — explicit Gemini provider lineage

Current main allocator knows ChatGPT, DeepSeek and xAI adapters but not Gemini.
Add explicit adapter lineage only after official adapter semantics are accepted.

Expected mapping:

- official Google My Activity Gemini adapter -> gemini;
- NotebookLM-preserved Gemini adapter -> gemini.

The mapping says which provider family the evidence belongs to for allocation.
It does not erase source authority differences.

Acceptance:

1. unit tests for both adapter mappings;
2. unknown Gemini-like adapter names remain unresolved/fail-closed;
3. provider conflicts still fail closed;
4. rebuild/inspect allocation from the accepted corpus;
5. unresolved-provider rows are explained rather than silently assigned;
6. canonical allocator determines shard count/IDs and estimated size.

Do not hard-code one Gemini shard merely because current data is small.
gemini-0001 is an expectation only if allocator output proves it.

## Phase D — bounded promotion readiness

Run:

    tools/dev/serving-promotion-readiness \
      --manifest /private/path/gemini-promotion-readiness.json \
      --run-qa \
      --output /private/path/gemini-promotion-readiness-receipt.json

The first Gemini shard is a NEW_SHARD, not a replacement. Therefore the generic
readiness contract must support previous_generation_id=null only when the shard
is genuinely new.

Requirements:

- existing replacement shards must still require a concrete previous generation;
- NEW_SHARD must retain null rollback generation instead of inventing one;
- source/bundle/READY proofs, finite bounds and counts remain mandatory;
- unchanged catalog members remain explicit;
- no secrets, DB URLs, private Drive IDs or transcript text enter the manifest;
- terminal local state is only READY_FOR_RUNTIME_GATES, never "promoted".

This phase is the proof that the bounded-runner pattern works for a real new
provider, not merely synthetic replacement fixtures.

## Phase E — Gemini data plane

For each allocator-produced Gemini shard:

1. run scripts/verify_shard_source.py against the accepted corpus;
2. require VERIFIED;
3. if verifier reports unsupported route semantics, extend the verifier contract
   with tests; do not bypass it;
4. build the shard bundle twice and require byte-identical SHA-256;
5. stage the exact private bundle in Drive and independently read back SHA-256;
6. materialize a fresh Neon DATA project through the canonical Arcade BUILDING
   workflow;
7. independently read back the exact BUILDING generation/count/provenance;
8. promote through the canonical READY workflow;
9. independently read back READY;
10. create/verify the least-privilege serving login.

The data project is not automatically a Function host.

## Phase F — first Gemini serving bootstrap

Because Gemini has no existing shard Function host, this is a provider bootstrap,
not a routine data-only refresh.

Do not expose/rotate the shared shard bearer merely to make a new project
self-contained.

Thin current route:

- inherit reviewed shard Function source/auth from an already accepted live
  shard Function-host lineage;
- bind only the least-privilege Gemini DB URL;
- keep this hosting choice explicitly transitional.

A future dedicated Function-host architecture is separate work.

Candidate shard acceptance:

- GET /health -> 200;
- unauthenticated POST /search -> 401;
- authenticated positive exact-generation control;
- authenticated negative control;
- exact Gemini shard/generation provenance.

Candidate router:

- branch from current production router;
- preserve auth/telemetry/PostHog/feedback;
- add Gemini catalog entry with finite time_min/time_max;
- keep existing ChatGPT/DeepSeek/xAI entries unchanged;
- do not invent a Gemini source watermark.

Required candidate acceptance:

- provider=gemini positive -> FOUND;
- provider=gemini negative -> NO_SIGNAL;
- broad search selects intended catalog with unavailable_shards=[];
- exact Gemini generation provenance;
- Gemini coverage finite-bounds classification is available.

## Gemini currentness semantics

The official My Activity export is stronger provider-origin evidence than the
NotebookLM rescue lane. Google documents that Gemini Apps Activity can be
exported through Takeout, while also documenting explicit classes that are not
part of that activity surface: Temporary Chats, chats made while History is
off, and activity deleted manually or by retention policy. Google also warns
that changes made after an export request and before archive creation may be
absent from the archive.

For the accepted private export, independent provider evidence establishes:

- Takeout request receipt: 2026-11-21T15:45:28Z;
- archive-ready receipt: 2026-11-21T15:47:44Z;
- the archive filename carries the same request timestamp;
- parsed Gemini prompt activity spans 2025-03-22T03:41:03Z through
  2026-11-21T15:28:40Z;
- 0 of 2,106 parsed prompt activities are later than the request receipt.

This supports a bounded provider-export claim that is stronger than
UNKNOWN_WITHOUT_SOURCE_WATERMARK but weaker than provider-complete account
history.

Target serving semantics:

    source_currentness=BOUNDED_PROVIDER_EXPORT_SNAPSHOT
    source_scope=GEMINI_APPS_ACTIVITY
    observed_through=2026-11-21T15:45:28Z
    basis=provider_takeout_request_receipt

The claim means: the indexed source is the Gemini Apps Activity snapshot
exported by Google for that Takeout request boundary. It does **not** mean every
Gemini conversation that ever existed is present.

Explicit exclusions remain outside this scope:

- Temporary Chats;
- chats created while Gemini History was off;
- activity already manually deleted or auto-deleted before the export;
- data excluded by other documented Google retention/deletion rules.

The archive-ready timestamp must not be used as the upper bound because Google
explicitly warns that changes between request time and archive creation may be
missing.

Do not map this evidence to PROVIDER_REPORTED_NO_KNOWN_GAPS: Google does not
make that stronger claim. Runtime support for BOUNDED_PROVIDER_EXPORT_SNAPSHOT
must be added and tested separately before production Gemini serving uses it.

## Production cutover

Preserve:

- production router project/branch/Function identity;
- production router URI;
- existing Arcade worker ID;
- existing App/plugin identity;
- router/shard auth;
- telemetry/PostHog/feedback;
- existing ChatGPT/DeepSeek/xAI generation bindings.

Mutation sequence:

1. add allocator-verified Gemini catalog entry/entries;
2. keep Gemini source watermark absent unless independently authorized/proven;
3. verify all unrelated environment keys remain;
4. force fresh runtime deployment on the same production router URI;
5. directly verify protected production metadata;
6. refresh the existing Arcade worker tools;
7. use fresh App-backed discovery/readback.

Terminal App acceptance:

- Gemini-only coverage sees expected generation/bounds and
  UNKNOWN_WITHOUT_SOURCE_WATERMARK;
- Gemini-only positive search -> FOUND, unavailable_shards=[];
- Gemini-only negative control -> NO_SIGNAL, unavailable_shards=[];
- broad search includes Gemini where relevant with zero unavailable;
- existing ChatGPT/DeepSeek/xAI controls remain healthy;
- current production router/App identities are unchanged.

## Rollback

The pre-Gemini four-shard production catalog is rollback authority until final
disposition.

Rollback is catalog restore -> same-URI runtime reload -> Arcade refresh ->
fresh App-backed readback.

Do not delete the accepted official Gemini corpus merely because serving is
rolled back.

## Completion evidence

Public-safe final receipt should include:

- merged official adapter head;
- accepted corpus/recovery identity and aggregate counts;
- allocator public summary;
- Gemini shard ID/generation and bounded time range;
- shard bundle SHA-256;
- Neon READY evidence;
- bounded readiness receipt digest;
- candidate shard/router acceptance;
- production same-URI reload;
- Arcade refresh;
- fresh App-backed Gemini coverage/search provenance;
- rollback retention.

Never publish private conversation text, Google Takeout locators, Drive IDs,
database URLs or credentials.
