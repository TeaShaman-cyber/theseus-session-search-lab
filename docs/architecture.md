# Architecture

## Layers

```text
capture adapter
  -> transport/storage adapter
  -> immutable or content-addressable session artifact
  -> importer/normalizer
  -> searchable projection
  -> assistant working context
```

The assistant consumes search results as historical evidence. It must not treat the index as live operational authority.

## Replaceability

- Browser capture is replaceable.
- Barn Doctor is replaceable.
- Google Drive is replaceable.
- MarcoPolo is a current development/operations environment and is replaceable; it is not an architectural runtime dependency.
- SQLite FTS5 is the current projection implementation and may be replaced if the artifact/verification contract remains intact.

## Current development prototype

The initial prototype established that a captured session export can be integrity-checked, normalized, projected into SQLite FTS5, and searched. The real corpus remains private; this public repository reproduces the mechanism with synthetic fixtures.

Current main also includes two transport-neutral operational seams:

- `session_search.handoff` performs a one-shot ingest from a directory of portable ZIP artifacts and records private receipts;
- `session_search.refresh_readiness` compares inbox artifact hashes with accepted-ledger membership without claiming live-provider freshness.

Neither seam makes its transport, scheduler, or development environment authoritative.

## Operational binding and promotion boundary

A deployed runtime may keep a stable pointer to a versioned corpus directory, for example through an environment variable, explicit CLI path, or externally managed symlink. This binding is intentionally outside the evidence contract:

```text
immutable evidence authority         mutable operational state
----------------------------         -------------------------
accepted ledger + artifact blobs --> versioned derived corpus
                                      ^
                                      |
                               stable runtime binding
```

The stable binding may be switched atomically after a candidate reaches the required deployment postconditions. The previous target can remain available for fast rollback. Loss or replacement of the binding must not rewrite accepted evidence, and a bound projection remains disposable derived state.

Promotion and verification are distinct claims. A binding readback can prove which candidate is being served; it does not prove byte-level derivation from accepted evidence. Full corpus verification remains the stronger mechanism for that claim.

## Target direction

The long-term target can remove the browser entirely if an authoritative session-history source becomes available. Session Search should survive that transition because its stable boundary is the portable artifact/import contract, not the capture implementation.

## Optional provenance recovery layer

The normal path assumes a portable artifact already has one unambiguous session boundary. Browser/network captures can violate that assumption by collecting unrelated provider fetches in one raw export.

When this occurs, an optional provenance-aware adapter stage may derive a single-session portable artifact before the normal importer:

```text
raw mixed capture
  -> provenance-aware recovery adapter
  -> portable single-session artifact + transformation receipt
  -> importer/normalizer
```

This layer is not permission to weaken importer validation. Ambiguous membership remains blocked, and the immutable raw capture stays source evidence. See [Mixed capture artifact recovery](mixed-artifact-recovery.md).
