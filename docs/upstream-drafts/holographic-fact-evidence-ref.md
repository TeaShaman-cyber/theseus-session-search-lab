# Draft upstream proposal: optional evidence reference for Holographic facts

> Internal draft for the Holographic memory provider bundled in `NousResearch/hermes-agent`. Do not publish upstream without explicit external-mutation approval.
>
> Upstream revision inspected: `c8aa5608c24e3636e77c267650c0f1f52e44adb0` (2026-09-08 audit refresh).

## Feynman version

**Trust answers "how much should I rely on this fact?"**

**Provenance answers "where did this fact come from?"**

Those are different questions.

Holographic already has a useful fact-memory model with trust scoring, contradiction/entity reasoning, and optional HRR retrieval. The current `fact_store` contract exposes fact content plus fields/actions around category, tags, trust, search, probing, reasoning, contradiction, update, removal, and listing. At the inspected revision, no generic `evidence_ref` / `source_ref` field was found in that tool contract.

## Problem

A durable fact can later become stale, surprising, or contradictory. Trust feedback can tell the system that confidence/usefulness changed, but it cannot by itself answer:

```text
Why did this fact enter memory?
Which session, note, import, or receipt supported it?
```

Without a lightweight link back to evidence, audit and repair require semantic rediscovery.

## Proposed invariant

A fact may optionally carry an **opaque evidence reference** that is returned with the fact but is not interpreted as trust or authority.

The deliberately small first version is one nullable string:

```text
evidence_ref: string | null
```

Example tool call:

```json
{
  "action": "add",
  "content": "The production deploy window is Tuesday evening.",
  "category": "operations",
  "tags": "deploy,schedule",
  "evidence_ref": "@session:default/abc123"
}
```

Example returned fact:

```json
{
  "fact_id": 42,
  "content": "The production deploy window is Tuesday evening.",
  "category": "operations",
  "trust": 0.7,
  "evidence_ref": "@session:default/abc123"
}
```

The reference is intentionally opaque. Holographic does not need to dereference or validate every possible provider URI.

## Why one optional string first?

A list of sources, a provenance graph, automatic evidence scoring, and deletion cascades are all plausible future features — but none is required to test the core value.

A nullable column keeps the first change cheap:

```sql
ALTER TABLE facts ADD COLUMN evidence_ref TEXT;
```

If real use demonstrates that multiple independent evidence references are common enough to matter, that can justify a later normalized relation/table. Do not pre-build it.

## Suggested semantics

- `evidence_ref` is optional on `add`;
- retrieval/list/search returns it when present;
- an explicit `update` may replace or clear it if maintainers want mutable provenance;
- adding duplicate fact content should **not silently rewrite provenance** as a side effect; existing duplicate semantics should remain stable unless an explicit update is requested;
- trust updates do not implicitly modify `evidence_ref`;
- presence of an `evidence_ref` does not increase trust automatically;
- absence of an `evidence_ref` does not make a fact invalid;
- an evidence reference may itself contain a sensitive local identifier, so it follows the same storage/export privacy boundary as the fact and never grants permission to dereference the target.

Automatic extraction may later attach the current Hermes session reference when one is reliably available, but that is a follow-up and not required for the first implementation.

## Non-goals

This proposal does **not** ask Holographic to:

- import Theseus artifacts, ledgers, receipts, or coverage states;
- validate permissions or authority represented by the reference;
- automatically dereference arbitrary providers;
- calculate trust from provenance;
- implement a general provenance graph;
- delete source evidence when a fact is removed, or vice versa;
- require provenance for every existing fact.

## Compatibility

Existing databases can keep `NULL` for all current rows. Existing `fact_store add` callers remain valid because the field is optional.

The conceptual separation should remain explicit:

```text
trust        = confidence/usefulness dynamics inside fact memory
evidence_ref = traceability back to supporting evidence
```

## Acceptance sketch

A minimal implementation should demonstrate:

1. existing facts and tool calls work unchanged when `evidence_ref` is omitted;
2. a fact added with `evidence_ref` returns the same opaque value through normal retrieval/list/search;
3. trust feedback/update does not mutate provenance implicitly;
4. duplicate-content handling does not silently replace an existing evidence reference;
5. old databases migrate with a nullable column and no required backfill;
6. no source dereferencing, permission transfer, or provenance graph is introduced.

## Internal five-mask review outcome

- **Implementer:** PASS — one optional field is enough to test the value.
- **Spec / Invariant:** PASS — trust and provenance remain orthogonal.
- **Adversary:** PASS with guard — duplicate `add` must not silently overwrite provenance.
- **Maintainer:** PASS after YAGNI cut — single opaque ref now; no source table until evidence demands it.
- **Evidence / Security:** PASS — reference carries traceability, not authorization or automatic trust.

## Upstream conversation opener

Would Holographic maintainers be interested in an optional opaque `evidence_ref` on facts so a stored fact can point back to the session/note/import that supported it, without changing trust semantics or introducing a provenance subsystem? If that direction fits the provider, the first implementation can stay to one nullable field plus tool round-trip support.
