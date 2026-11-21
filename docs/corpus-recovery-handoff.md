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

## Operational checkpoint — 2026-11-21 ChatGPT refresh

Issue [#302](https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/302)
refreshed the accepted ChatGPT evidence lane from a newly received official OpenAI
account export using the existing `session_search.chatgpt_export` adapter. No adapter
code change was required.

Observed source-shape and semantic delta:

- the export exposed three numbered `conversations-*.json` members;
- 252 valid provider conversations materialized as 543 branch artifacts;
- 2 exact empty records were skipped under the existing adapter contract;
- compared with the most recent previously accepted official ChatGPT snapshot,
  210 provider conversations were semantically unchanged, 10 changed, 32 were new,
  and 1 previously observed conversation was absent from the newer snapshot;
- branch artifacts moved from 474 in the previous snapshot to 543 in the new one.

The accepted candidate after reconciliation contained:

- 1,829 immutable accepted artifacts;
- 694 projected sessions;
- 348,559 projected messages;
- 203,441 searchable/FTS rows;
- source-adapter artifact counts: 1,693 ChatGPT, 43 DeepSeek, 33
  NotebookLM-preserved Gemini, and 60 xAI/Grok.

The recovery generation was published through the normal ordering above. Drive byte
readback, immutable receipt readback, current-pointer readback, and the publisher's
fresh-runtime recovery probe all succeeded. A second recovery into an empty work
directory also verified the transport, decoded projection, and corpus lock. Expanding
that independently downloaded accepted-evidence archive reproduced the same 1,829
artifact / 694 session / 348,559 message / 203,441 searchable-row projection.

This checkpoint does **not** upgrade source currentness. `corpus status` still reports
`UNKNOWN_WITHOUT_SOURCE_WATERMARK`; receiving a newer account export is evidence of
a newer accepted snapshot, not proof that OpenAI exposes every live conversation or
that no newer account state exists.

### Performance evidence

This refresh also reproduced both existing performance concerns:

- [#59](https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/59):
  210 of 221 previously observed provider conversations were semantically unchanged,
  yet the current reconciled path still rebuilt the full projection from the enlarged
  accepted set.
- [#60](https://github.com/TeaShaman-cyber/theseus-session-search-lab/issues/60):
  after the batch full-corpus verification gate had already succeeded, the current
  receipt-emission loop ran a full SQLite `PRAGMA integrity_check` once per input
  artifact. With 543 input artifacts this produced severe redundant read amplification.

The operational run was stopped only after standard ingest receipts proved execution
had crossed the full-verification gate. One batch SQLite integrity check then returned
`ok`; all 543 input artifacts were individually rechecked for immutable blob,
accepted-ledger, projection-membership, and routing postconditions with per-artifact
SQLite integrity explicitly deferred to that batch check. Missing receipts were then
completed through the existing receipt writer. This is an operational workaround, not
a replacement verification contract; the code-level optimization remains tracked in
#60.
