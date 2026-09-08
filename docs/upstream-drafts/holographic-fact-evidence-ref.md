# Draft design disposition: defer a generic Holographic `evidence_ref`

> Internal design note for the Holographic memory provider bundled in `NousResearch/hermes-agent`. Do not publish upstream without explicit external-mutation approval.
>
> Baseline upstream revision inspected during audit: `c8aa5608c24e3636e77c267650c0f1f52e44adb0` (2026-09-08). Current documentation refresh still describes Holographic as a local SQLite fact store with trust scoring, entity resolution, FTS5, and optional HRR. Exact source reread was temporarily rate-limited during this revision, so the baseline commit remains the pinned code witness.

## Feynman version

The original idea was simple:

```text
trust       = how much should this fact be relied on?
provenance  = where did this fact come from?
```

That distinction still looks useful.

But the first proposed implementation — one nullable opaque `evidence_ref` column on each fact — is **too small to be correct** against the current fact-store semantics.

The right outcome of review is therefore not to make the field more complicated until it passes. It is to **defer the feature** until a native, scope-safe source primitive or a real multi-evidence requirement exists upstream.

## Why the original single-field design does not survive review

### 1. Duplicate content creates a lossy choice

Current Holographic facts are content-unique. Consider:

```text
add fact X with evidence A
add same fact X with evidence B
```

A single mutable field has only bad implicit choices:

```text
drop B        -> new evidence is silently lost
overwrite A   -> provenance history is silently rewritten
```

A correct design needs either:

- an explicit conflict/result contract that refuses the second evidence mutation, or
- support for multiple evidence relations.

The first option preserves correctness but weakens the usefulness of "add with evidence". The second option is already more than one nullable field.

### 2. Content updates can make evidence false

If a fact changes from:

```text
"Deploy window is Tuesday" + evidence A
```

to:

```text
"Deploy window is Thursday"
```

keeping evidence A may falsely imply that A supports the new assertion. Automatically clearing A is also an implicit provenance mutation.

A correct design therefore needs an atomic rule such as:

```text
content changes
-> caller must explicitly replace evidence, clear evidence, or reject the update
```

That is a real lifecycle contract, not a passive nullable column.

### 3. Fact visibility is not source visibility

The most important failure is authority/privacy:

```text
caller may see fact
!=
caller may see source session/note/import
```

A generic `@session:...`, URI, local path, note ID, or provider handle can reveal a source identity across profile/runtime/provider boundaries. If the caller separately has a lookup capability, it may also be able to dereference that handle.

Saying "the reference grants no permission" is not enough if returning the reference itself crosses a visibility boundary.

Therefore a generic arbitrary reference is not safe unless the provider has an existing native primitive whose **visibility/redaction and dereference checks are enforced in the same scope**.

## Graph-join result

The three review findings interact:

```text
duplicate evidence
        \
         -> single mutable field is insufficient
        /
content update

visibility boundary
        -> generic dereferenceable ref is unsafe
```

Solving only one finding does not produce a valid contract.

A normalized evidence relation could address duplicate evidence, and content-version binding could address stale updates, but together they begin to form a provenance subsystem. That fails the current YAGNI bar because the upstream need has not yet demonstrated that complexity.

## Current disposition

```text
problem statement:         SURVIVES
trust != provenance:       SURVIVES
generic evidence_ref:      REJECTED FOR NOW
new provenance subsystem:  YAGNI / NOT JUSTIFIED
upstream feature proposal: DEFER
```

This is not a claim that provenance is unimportant. It is a claim that the currently evidenced problem does not yet justify a safe generic interface.

## What would make the candidate viable again?

Re-open this proposal only if at least one of these becomes true upstream:

1. **Native same-scope source primitive exists.** Holographic/Hermes exposes a source identity whose visibility and dereference permissions are enforced within the same profile/runtime boundary.
2. **Repeated multi-evidence demand appears.** Real workflows show that the same fact commonly needs multiple independent evidence links, justifying a small normalized relation rather than a scalar field.
3. **Fact revisions become first-class.** The provider gains revision/version semantics that can bind evidence to the assertion version it actually supports.
4. **Maintainers explicitly want provenance lifecycle semantics.** Then duplicate, update, deletion, redaction/export, and permission behavior can be designed as one coherent contract rather than accumulated piecemeal.

## If maintainers already have a native same-scope primitive

A future minimal design could be reconsidered with these hard requirements:

- only that native same-scope source identity is accepted; arbitrary URIs/paths/provider handles are rejected;
- source visibility is enforced independently from fact visibility;
- duplicate `add` with different evidence never silently drops or overwrites evidence;
- content-changing `update` requires an explicit evidence decision atomically;
- evidence never changes trust automatically;
- presence of evidence never grants authority;
- absence of evidence does not invalidate a fact;
- existing facts remain valid without backfill.

These are preconditions for a future design, not a proposed v1 schema today.

## Non-goals

This disposition does **not** propose:

- a generic `evidence_ref` column;
- a provenance graph or new evidence table today;
- arbitrary source dereferencing;
- cross-profile/runtime/provider authority transfer;
- trust derived from provenance;
- deletion cascades between facts and source evidence;
- retroactive provenance backfill for existing facts.

## Compatibility

No implementation change is proposed, so current Holographic databases and tool contracts remain untouched.

## Acceptance sketch for this design disposition

This internal draft is ready when it demonstrates:

1. the provenance gap is stated separately from trust;
2. the single-field design is explicitly withdrawn rather than silently patched around its contradictions;
3. duplicate-content evidence loss is documented;
4. content-update stale-evidence risk is documented;
5. fact/source visibility separation is documented;
6. reopening conditions require a native scope-safe primitive or demonstrated lifecycle need;
7. no provenance subsystem is proposed without stronger evidence.

## Internal review outcome

### Five-mask pass after graph reconciliation

- **Implementer:** PASS as DEFER — no premature schema migration.
- **Spec / Invariant:** PASS — duplicate, update, and visibility requirements are now treated together.
- **Adversary:** PASS — generic source handles are no longer assumed safe because they are opaque.
- **Maintainer:** PASS — avoids growing a provenance subsystem from a convenience field.
- **Evidence / Security:** PASS — `evidence != authority` and `capability != permission` are enforced as design gates, not prose disclaimers.

### Codex exact-head review incorporation

Accepted P2 findings:

1. **"Define a non-lossy duplicate evidence policy."**
2. **"Prevent content updates from retaining stale evidence."**
3. **"Do not equate fact visibility with source visibility."**

Disposition: all three are structurally valid. Rather than add three new mechanisms to rescue a one-column proposal, the generic `evidence_ref` feature is deferred until upstream exposes a scope-safe primitive or evidence justifies a fuller lifecycle model.

## Possible future maintainer question

Does Holographic/Hermes already have, or plan to introduce, a native source-identity primitive whose visibility and dereference permissions are guaranteed within the same profile/runtime scope? If not, this design note recommends **not** adding a generic fact-level `evidence_ref` yet.
