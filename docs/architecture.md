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
- MarcoPolo is development-only and replaceable.
- SQLite FTS5 is the current projection implementation and may be replaced if the artifact/verification contract remains intact.

## Current development prototype

The initial prototype established that a captured session export can be integrity-checked, normalized, projected into SQLite FTS5, and searched. The real corpus remains private; this public repository reproduces the mechanism with synthetic fixtures.

## Target direction

The long-term target can remove the browser entirely if an authoritative session-history source becomes available. Session Search should survive that transition because its stable boundary is the portable artifact/import contract, not the capture implementation.
