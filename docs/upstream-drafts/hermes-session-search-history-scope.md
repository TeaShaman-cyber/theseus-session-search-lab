# Draft upstream proposal: make `session_search` zero-result misses epistemically safe

> Internal draft for `NousResearch/hermes-agent`. Do not publish upstream without explicit external-mutation approval.
>
> Baseline upstream revision inspected during audit: `c8aa5608c24e3636e77c267650c0f1f52e44adb0` (2026-09-08). A later current-doc refresh still shows local `state.db` + FTS5 search and default 90-day auto-pruning. Exact source reread was temporarily rate-limited during this revision, so the baseline commit remains the pinned code witness.

## Feynman version

A search miss proves only:

> **"I did not find it in the history this search could see."**

It does not prove:

> **"this never happened."**

Hermes already exposes useful boundaries *inside* a found session (`messages_before`, `messages_after`) and separately reports FTS rebuild state. The smallest missing guard is not a new history-scope model. It is a warning that prevents a zero-result search from being promoted into a historical absence claim.

## Current behavior motivating this proposal

At the pinned upstream revision and current documentation refresh:

- `session_search` searches messages stored in local Hermes session storage using FTS5;
- discovery exposes per-hit/session-local navigation boundaries;
- index rebuild/readiness is already represented separately;
- ended sessions may be automatically pruned according to configuration (default retention currently documented as 90 days);
- imported history may be a clean transcript rather than a byte-for-byte source replay.

These facts make zero hits useful retrieval evidence, but not completeness evidence.

Configured retention describes a **policy**. It does not identify the exact store searched, prove that the store was never manually cleaned or partially restored, prove that another profile/runtime has no relevant history, or prove complete importer coverage.

## Proposed invariant

`session_search` should make it difficult for an agent to turn a zero-result discovery into a stronger historical claim than the searched store supports.

### Smallest native change

When discovery returns zero matches, add a short response note/warning such as:

```json
{
  "success": true,
  "query": "old deployment incident",
  "results": [],
  "count": 0,
  "history_note": "No matches were found in the currently searchable session store. This is not proof that the conversation never occurred."
}
```

The field name and exact wording are intentionally non-normative. The contract is:

```text
zero search hits
  + no demonstrated completeness boundary
  -> historical absence remains UNKNOWN
```

A tool-description instruction could express the same invariant if maintainers prefer not to add a response field. The proposal prefers whichever representation is smallest and reliably visible to the agent on zero-result calls.

## Why this draft no longer proposes `history_scope` from retention settings

Review caught a category error in the first draft:

```text
retention/pruning configuration
!=
actual searched boundary
```

Two different or partially restored `state.db` stores can share the same pruning configuration. Returning `retention_days=90` under a name like `history_scope` therefore looks more informative than it really is.

If Hermes later has an observed, enforceable searched-store boundary (for example an explicit store/profile identity plus a demonstrated coverage interval), richer scope metadata may become justified. Until then, do not manufacture scope from policy settings.

## Index state stays separate

```text
index_rebuild = is the derived FTS projection ready?
zero-result note = what claim may be made from this miss?
store completeness = UNKNOWN unless independently evidenced
```

A complete FTS rebuild over an incomplete store is still an incomplete historical witness.

## Non-goals

This proposal does **not** ask Hermes to:

- add a new database table or persistence subsystem;
- expose retention configuration as proof of searched scope;
- adopt Theseus portable artifacts, accepted ledgers, coverage receipts, or rebuild machinery;
- become a cross-provider historical archive;
- change current pruning behavior;
- scan extra stores on every query;
- solve semantic memory or long-term fact synthesis.

Hermes native recall should remain simpler than Theseus Session Search.

## Compatibility

The change can be additive and zero-result-only. Existing successful-result shapes need not change.

If maintainers prefer a tool-description rule rather than response metadata, callers see no schema change at all.

## Acceptance sketch

A minimal implementation should demonstrate:

1. positive search results and ranking are unchanged;
2. a zero-result discovery exposes an agent-visible warning/note that the miss is not proof of historical absence;
3. the warning does not claim a searched coverage interval or store completeness that Hermes cannot observe;
4. pruning/retention configuration is not relabeled as completeness evidence;
5. FTS `index_rebuild` state remains a separate concern;
6. no new persistence, artifact, or provenance subsystem is introduced;
7. a test covers the user-facing invariant: zero hits over current searchable history must not be represented as "never happened".

## Internal review outcome

### Five-mask pass

- **Implementer:** PASS — zero-result note/tool-description guard is smaller than store-scope metadata.
- **Spec / Invariant:** PASS — distinguishes retrieval miss, index readiness, and completeness.
- **Adversary:** PASS — avoids false certainty after pruning, manual cleanup, partial restore/import, or profile/runtime separation.
- **Maintainer:** PASS after YAGNI reduction — no new store model.
- **Evidence / Security:** PASS — does not expose additional content or invent authority.

### Codex exact-head review incorporation

Accepted P2: **"Report the searched boundary instead of pruning policy."**

Disposition: the proposal no longer pretends retention settings describe the searched boundary. Because current Hermes does not demonstrate a completeness boundary, the smallest safe upstream change is warning-first. Rich scope metadata is deferred until there is actual boundary evidence.

## Upstream conversation opener

Would Hermes maintainers be open to making zero-result `session_search` calls explicitly non-conclusive — either via a small response note or equivalent tool-description guidance — so agents do not turn "no matches in the current searchable store" into "this never happened"? This intentionally avoids adding history-scope metadata until Hermes can observe a real searched-store boundary.
