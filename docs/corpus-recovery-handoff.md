# Accepted corpus recovery handoff

This contract removes cloud-shell archaeology after a Session Search corpus refresh.

## Authority

Accepted artifact evidence remains source authority. Google Drive is private durable
transport. Neon is a rebuildable serving projection. The recovery handoff is only a
locator and byte-identity contract; it cannot make an unverified candidate accepted.

## Two private objects

Every accepted corpus publishes:

1. an immutable `theseus.session-search-recovery-receipt.v1` containing the exact
   Drive identities and byte digests for the transport projection and accepted-evidence
   archive, the decoded SQLite identity/schema, accepted-set identity, source commit,
   verification disposition, and optional source watermark;
2. one mutable `theseus.session-search-recovery-locator.v1` current pointer that
   binds the exact bytes of one immutable receipt.

Private Drive IDs, corpus digests, filenames, and source identifiers never enter the
public repository or public issue comments.

## Acceptance ordering

A new corpus is not operationally complete until this sequence succeeds:

```text
accepted evidence + verified projection
  -> upload recovery artifacts
  -> independent byte/hash readback
  -> build immutable receipt
  -> upload + read back exact receipt
  -> build current locator from exact receipt bytes
  -> update current pointer with rollback-on-ambiguous-readback
  -> fresh-runtime recovery probe
  -> verify transport/evidence/decoded SQLite
  -> create private local corpus lock
```

The current pointer is updated last. Failure before that leaves the previous accepted
pointer untouched. An ambiguous pointer update must restore and independently read
back the previous pointer before returning failure.

## Fresh runtime recovery

A fresh governed shell needs only:

- exact Git checkout of this repository;
- its authorized Google Drive access;
- the provider adapter that discovers the single current pointer by protocol metadata.

The normal path is:

```text
Drive current pointer
  -> exact immutable receipt
  -> exact projection/evidence artifacts
  -> decode projection
  -> python -m session_search.recovery_handoff verify-local
  -> private corpus lock
```

No Session Search query, GitHub issue history, old runtime disk, remembered filename,
or wall-clock "newest file" heuristic is part of recovery.

## Provider boundary

This repository owns the receipt/locator schema and validators. Provider-specific
Drive discovery/upload/download mechanics belong in the selected governed cloud-shell
adapter. The current exe.dev implementation is coordinated by
`TeaShaman-cyber/theseus-cloud-shell#12`.

## Failure states

- no current pointer: `RECOVERY_POINTER_UNAVAILABLE`;
- more than one current pointer: `RECOVERY_POINTER_AMBIGUOUS`;
- receipt byte mismatch: `RECOVERY_RECEIPT_*_MISMATCH`;
- artifact mismatch: `RECOVERY_ARTIFACT_*_MISMATCH`;
- decoded projection mismatch: `RECOVERY_DECODED_*_MISMATCH`;
- corpus schema mismatch: `RECOVERY_PROJECTION_SCHEMA_MISMATCH`.

These are recovery-path failures only. They do not imply that historical evidence or
the live serving corpus is absent.
