# Mixed capture artifact recovery

`normalize_artifact()` intentionally fails closed when one portable artifact exposes more than one stable conversation identity:

```text
BLOCKED_MIXED_SESSION_ARTIFACT
```

That invariant must not be weakened merely because a capture appears to contain one dominant conversation. Browser/network capture can record unrelated provider fetches from neighboring or parent conversations in the same export.

## Observed failure class

A sanitized development capture contained:

- one detail payload for the active conversation;
- one unrelated `conversation_get` detail payload for another conversation;
- many paginated `conversation_messages` payloads;
- network events proving every message-page request belonged to the active conversation.

The raw ZIP was therefore mixed at the capture boundary even though the historical message pages had one unambiguous owner. The importer correctly rejected the raw artifact.

## Recovery boundary

Recovery is a preprocessing/adapter concern before the portable importer:

```text
immutable mixed source capture
        ↓
provenance-aware recovery
        ↓
derived single-session artifact + recovery receipt
        ↓
normal portable importer
        ↓
regeneratable projection
```

The core importer remains unchanged and continues to reject mixed identity.

## Authority order

Member selection may use only evidence that binds a captured payload to a provider conversation:

1. captured request provenance (`conversationId`, request key, endpoint class);
2. an explicit provider conversation identifier inside the payload when its semantics are defined by the adapter;
3. otherwise fail closed.

Do not recover membership from:

- title equality;
- filename order;
- nearest timestamps;
- UI branch names;
- message text similarity;
- majority vote over payloads.

If provenance is incomplete or contradictory, the disposition remains blocked/unknown.

## Derived artifact receipt

The immutable source ZIP remains historical evidence. A derived artifact must record at least:

```json
{
  "schema": "theseus.session-search-recovery.v1",
  "source_sha256": "<source hash>",
  "derived_sha256": "<derived hash>",
  "selected_session_id": "<stable provider session id>",
  "included_members": ["<member>"],
  "excluded_members": ["<member>"],
  "provenance_rule": "<bounded rule used to prove membership>"
}
```

A receipt is evidence about the transformation; it does not replace the source capture.

## Barn Doctor request provenance

For Barn Doctor-style captures, `network-events.jsonl` can provide the binding between an optional payload request key and the provider conversation ID. A recovery implementation must validate all relevant request keys, not infer that adjacent optional filenames belong to the same conversation.

For paginated history, the recovery gate should be able to state a bounded result such as:

```text
N/N captured conversation_messages requests
bind to selected_session_id
```

Any conflicting owner blocks materialization.

## Archive handling

A recovery tool must regenerate the manifest to match the derived members and must verify hashes before normal import. ZIP container metadata is not semantic authority. Some capture ZIPs carry timestamps that require tolerant container reconstruction; changing container timestamps is acceptable only when member bytes and the recovery receipt remain verifiable.

## Scope

Issue #16 tracks implementation and synthetic regression fixtures. Until that implementation exists, manual recovery is a diagnostic procedure only and must not be promoted to an automatic corpus-ingest path.
