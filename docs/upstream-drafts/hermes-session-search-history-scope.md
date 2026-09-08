# Draft upstream proposal: make `session_search` misses epistemically safe

> Internal draft for `NousResearch/hermes-agent`. Do not publish upstream without explicit external-mutation approval.
>
> Upstream revision inspected: `c8aa5608c24e3636e77c267650c0f1f52e44adb0` (2026-09-08 audit refresh).

## Feynman version

A search miss proves only **"I did not find it in the history I searched"**.
It does not prove **"this never happened"** when older sessions may have been pruned, imported incompletely, or otherwise absent from the current store.

Hermes already does a good job describing boundaries *inside* a found session: discovery can expose `messages_before` / `messages_after`, scroll can move around a message anchor, and the tool reports when its FTS index is still rebuilding. The missing piece is a small description of the scope of the history store itself.

## Current behavior motivating this proposal

At the inspected upstream revision:

- `session_search` reads actual messages from Hermes `state.db` and supports discovery, scroll, read, and browse modes;
- discovery reports per-hit session boundaries such as `messages_before` / `messages_after`;
- discovery already exposes an `index_rebuild` note when FTS backfill is incomplete;
- Hermes session cleanup is configurable and current documentation states automatic pruning is on by default with a 90-day retention default.

Those are useful local guarantees, but none of them by themselves make a zero-result search a proof of historical absence. A configured retention value describes policy; it is not proof that every session inside that window is present.

## Proposed invariant

`session_search` should make it difficult for an agent to turn a miss into a stronger claim than the store can support.

The smallest useful change is **additive response metadata** describing history/store scope. This does not require a new database, artifact format, ledger, or provider abstraction.

One possible shape:

```json
{
  "success": true,
  "mode": "discover",
  "query": "old deployment incident",
  "results": [],
  "count": 0,
  "history_scope": {
    "absence_is_conclusive": false,
    "auto_prune_enabled": true,
    "retention_days": 90,
    "note": "A search miss is not proof that the conversation never occurred."
  }
}
```

The exact field names are intentionally not normative. The important contract is:

```text
search miss
  + store may be incomplete
  -> historical absence remains unknown
```

If Hermes later gains explicit proof that a store is complete for a bounded interval, the metadata can become more precise. Until then, fail epistemically safe rather than infer completeness from configuration alone.

## Why not infer completeness from `auto_prune=false`?

Because pruning is only one way history can be incomplete. A database can also be:

- manually cleaned;
- restored from a partial backup;
- created after earlier conversations occurred;
- populated by an importer that did not capture everything;
- split across profiles or runtimes.

So `auto_prune=false` should not automatically imply `absence_is_conclusive=true`.

## Non-goals

This proposal does **not** ask Hermes to:

- adopt Theseus portable artifacts, accepted ledgers, or rebuild receipts;
- become a cross-provider historical archive;
- change the current pruning policy;
- add a new session database schema solely for this feature;
- make `session_search` slower by scanning additional history;
- solve semantic memory or long-term fact synthesis.

Hermes native recall should stay simpler than Theseus Session Search.

## Compatibility

The change can be additive: existing callers that ignore unknown response fields keep working.

`index_rebuild` and `history_scope` should remain separate concepts:

```text
index_rebuild = is the derived search index ready?
history_scope = what historical claims can the underlying store justify?
```

## Acceptance sketch

A minimal implementation should demonstrate:

1. existing discovery/search results are unchanged apart from additive metadata;
2. when automatic pruning is enabled, the response exposes the configured retention policy and does not claim a miss is conclusive;
3. when automatic pruning is disabled, the response still avoids claiming complete history without stronger evidence;
4. an in-progress FTS rebuild remains represented separately from store-level history scope;
5. no new persistence subsystem is required;
6. tests explicitly cover the agent-facing invariant: zero hits over a potentially incomplete store must not be described as proof that the event never happened.

## Internal five-mask review outcome

- **Implementer:** PASS — response metadata, not a new storage layer.
- **Spec / Invariant:** PASS — separates session-local boundaries, index readiness, and store completeness.
- **Adversary:** PASS — covers prune, manual deletion, partial restore/import, and profile/runtime gaps.
- **Maintainer:** PASS after scope reduction — no Theseus artifact machinery upstream.
- **Evidence / Security:** PASS — reports scope without exposing session content or inventing authority.

## Upstream conversation opener

Would Hermes maintainers be open to a small `session_search` response-level contract that exposes history-store scope/retention and explicitly prevents a zero-result search from being interpreted as proof of historical absence? If yes, field naming and the narrowest useful source of scope metadata can be agreed before a PR.
