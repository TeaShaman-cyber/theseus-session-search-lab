# Bounded session evidence delta — reference experiment

Related to #340, #338 and Dreaming Worker #65.

Capture a single explicitly selected projected session under the same corpus
mutation lock as the accepted membership receipt. Reuse projection consistency
verification. This is a correctness reference with explicit event and
source-link limits; it does not yet optimize the corpus-level verification
scan or discover the affected session closure.

The event key is the projected session ID plus existing local identity. A
version digest covers canonical message identity, role/class/content type,
text, provider message ID, observed time and provider order. Text is hashed
in-process, not returned. Absolute ordinal is excluded: inserting an earlier
event does not itself change every subsequent event's standalone identity.
An analysis using surrounding context/order must separately bind those
dependencies. The accepted set and source links are stored separately from
the event version. Scope and event IDs are sensitive metadata; keep receipts
within trusted custody. They are not authentication or read authorization.

Compare two validated receipts for the same scope and session. Return added,
removed, changed and provenance-only event identities separately, plus a
coverage-change flag. A repeated capture of unchanged content changes source
links, not the event version. Equal text in distinct events remains distinct.
Removed events (including potential branch rerouting), lost provenance and
coverage changes require reconciliation; do not silently discard prior
knowledge or infer no work. Provenance-only is a classification, not automatic
permission to skip analysis that depends on provenance. Global membership
change alone does not invalidate unchanged event fingerprints.

The receipt binds exact observed source artifact hashes, page positions and
source-object hashes to each projected event. It describes a projection,
not the raw source bytes or a verified complete history. Currentness is always
UNKNOWN_WITHOUT_SOURCE_WATERMARK. No evidence body transport, source cursor
advance, generation promotion, automatic inference or source authority change.
Limits bound returned row counts, not total query I/O or bytes; performance
and affected-session selection remain separate experiments.

Tests exercise actual synthetic ingest, recapture, distinct equal-text
events, unrelated sessions, time and coverage changes, rebuild equivalence,
bounds and corrupt receipts. A synthetic removal receipt tests classification,
not a demonstrated live rerouting case.
