# Spec: gemini-source-acceptance

Status: DRAFT FOR HUMAN REVIEW
Capability map: CAPABILITY_MAP-gemini.md
Tracking issue: #311
Existing implementation lineage: #299 / merged PR #301

## Objective

Accept the official Google My Activity Gemini Apps export into the durable Session Search corpus while preserving the already accepted NotebookLM rescue lane.

Success means the accepted corpus can be reconstructed and independently verified from immutable evidence, with official provider-origin evidence added without deleting, rewriting or semantically guessing rescue evidence.

## Tech Stack

- Python 3.11 repository runtime
- session_search.google_gemini_activity official Takeout adapter
- session_search.notebooklm_gemini preservation adapter
- existing corpus ledger/projection/recovery machinery
- governed Google Drive recovery transport
- Git/GitHub issue and receipt history for public-safe evidence

Normative accepted adapter identities for this initiative:

| Evidence lane | source_adapter |
|---|---|
| Official Google My Activity / Gemini Apps | google-gemini-myactivity |
| NotebookLM-preserved rescue evidence | notebooklm-preserved-gemini |

These identifiers are provenance, not interchangeable aliases.

## Commands

Inspect official export without mutation:

    python3 -m session_search.google_gemini_activity takeout.tgz --inspect

Inspect with exact-overlap reconciliation against an existing accepted corpus:

    python3 -m session_search.google_gemini_activity takeout.tgz --inspect --existing-corpus /private/path/session-search-corpus

Materialize portable official children:

    python3 -m session_search.google_gemini_activity takeout.tgz --output-dir /private/path/gemini-myactivity-artifacts

Ingest into a separate candidate corpus:

    python3 -m session_search.google_gemini_activity takeout.tgz --corpus /private/path/session-search-corpus-candidate

Canonical repository QA:

    ./tools/dev/check

## Project Structure

- session_search/google_gemini_activity.py — official provider-origin adapter
- session_search/notebooklm_gemini.py — preservation/rescue adapter
- session_search/corpus_store.py — accepted ledger and projection authority
- docs/corpus-recovery-handoff.md and recovery docs — durable recovery contract
- tests/ — adapter, corpus and reconstruction verification
- receipts/ — public-safe immutable acceptance evidence

## Code Style

Prefer explicit evidence states over inferred completeness. A source record must preserve source_adapter, hashes, provider conversation identity when observed, and unresolved state when identity cannot be proven.

Example policy shape:

    if exactly_one_provider_conversation_id:
        preserve_observed_identity()
    else:
        preserve_as_unresolved()
        do_not_guess()

Reconciliation is evidence-only and non-destructive. The existing official
adapter compares accepted rescue and official sessions by exact
canonical_message_sha256 sets, records unique/ambiguous/zero-overlap aggregate
facts, and does not rewrite either source lane. A best overlap is not permission
to delete or merge accepted artifacts.

## Recovery Authority

docs/corpus-recovery-handoff.md is the normative durable-recovery contract.
This module does not invent a second receipt format. Acceptance uses the
existing theseus.session-search-recovery-receipt.v1 immutable receipt,
theseus.session-search-recovery-locator.v1 current pointer, provider-side
byte/hash readback, and fresh-runtime verification through
python -m session_search.recovery_handoff verify-local.

## Testing Strategy

- adapter-focused deterministic tests for locale, prompt structure, provider IDs, timestamps and attachments;
- exact-overlap tests against NotebookLM rescue evidence;
- full artifact-derived candidate projection verification;
- independent empty-runtime recovery after durable publication using the normative recovery-handoff contract;
- canonical ./tools/dev/check before promotion.

Private transcript text must not appear in public test fixtures or receipts.

## Boundaries

Always:
- start from the current accepted recovery baseline;
- preserve official and rescue provenance separately;
- preserve unresolved prompt/attachment evidence rather than guessing;
- verify the full artifact-derived projection before durable promotion;
- read back consequential Drive bytes and recovery state.

Ask first:
- any destructive rewrite of accepted ledger history;
- any new source authority that changes which evidence is considered canonical;
- any migration that drops existing rescue artifacts.

Never:
- fold Google AI Mode into Gemini Apps implicitly;
- delete NotebookLM rescue evidence because official evidence overlaps it;
- publish private transcript text, Takeout locators, Drive IDs or credentials;
- claim all historical Gemini conversations are present from this module alone.

## Success Criteria

- the official adapter implementation from merged PR #301 is present in the exact source head and its exact-source dry-run remains structurally supported;
- official artifacts are ingested into a candidate built from the current accepted baseline;
- all previously accepted NotebookLM rescue artifacts remain accepted;
- full projection verification is VERIFIED;
- immutable recovery generation is published only after verification;
- uploaded bytes, receipt/current pointer and independent empty-runtime recovery are VERIFIED;
- aggregate official/rescue overlap and unresolved counts are recorded public-safely.

## Open Questions

- Current durable candidate/rebuild completion state must be read from the accepted recovery receipts at execution time; dated working-directory state is not authority.
