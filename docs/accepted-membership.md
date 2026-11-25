# Accepted membership delta — laboratory contract

Related to #338, capture/refresh #3 and Dreaming Worker #65.

Session Search's accepted artifact ledger owns membership. SQLite is a
projection. Neither a changed generation nor a search miss establishes which
artifacts were accepted.

`session_search.accepted_membership.capture_accepted_membership` captures an
explicitly scoped local corpus receipt under the existing mutation lock,
requiring the existing projection-consistency check to pass. It reuses
`artifact_overlap.set_digest` and `corpus_store.accepted_entry_digest`. Members
are ordered by artifact SHA-256, with the corresponding accepted ledger-entry
digest; the receipt includes a deterministic SHA-256 over canonical JSON
(sorted keys, compact separators, UTF-8, no ASCII escaping), excluding the
snapshot digest itself.

`compare_accepted_membership(previous, current)` validates both receipts and
returns the sorted newly accepted artifact identities, or UNCHANGED. Scope
mismatch, missing or malformed receipts, digest mismatch, duplicate members,
removed members and changed historical ledger entries fail closed. There is no
implicit bootstrap. The caller must establish and retain the previous receipt
and advance it only after the associated work is accepted. A scope label is not
an authorization token. Digests detect inconsistency; they do not authenticate
untrusted receipts.

The receipt describes the observed local accepted set at capture, not the
live provider's history. Currentness is always
`UNKNOWN_WITHOUT_SOURCE_WATERMARK`; projection consistency does not reverify
artifact bytes or full derivation. Added artifacts may overlap previously
accepted messages. This is not a message delta, automatic evidence reader,
scheduler, source watermark or new endpoint. No message bodies or corpus paths
are included; artifact identities and scopes still require appropriate custody.

Tests use actual synthetic Barn Doctor ingestion and SQLite, exercise
unchanged/new membership, drift, duplicate/invalid receipts, missing and locked
corpus, and reject projection inconsistency. No live corpus access is required.
