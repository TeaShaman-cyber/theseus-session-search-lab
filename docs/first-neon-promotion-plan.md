# First accepted-corpus promotion plan

Issue #75 defines the first real-corpus promotion procedure for the federated Neon serving architecture proven by #64 and sized by #61. This document is a plan only. It does not authorize uploading private corpus content or switching production traffic.

## Authority and privacy boundary

```text
accepted source evidence / immutable exports = authority
local accepted Session Search projection      = current derivation witness
Neon project shards                           = rebuildable derived serving state
router/catalog                                = derived routing state
```

The public repository must not contain private session IDs, message text, source IDs, Drive IDs, raw export filenames, private URLs, credentials, or private digests.

The allocation algorithm may inspect private session identifiers locally only as deterministic tie-break input. The private session-to-shard mapping is operational state and must not be committed to Git or posted in public issue receipts.

## Atomic allocation unit

The current corpus routes accepted artifacts to `projected_session_id` through `artifact_routes`. Therefore the initial promotion unit is the projected session.

A projected session is never split across Neon project shards.

If a future schema adds an explicit routing/conversation-family identity that is stronger than projected-session identity, the allocator must preserve that stronger atom instead of silently splitting a family.

## Sizing model

The #61 benchmark observed approximately:

- 9,789.7 Postgres bytes per searchable message for the representative synthetic shape;
- 5.5839 Postgres bytes per searchable text byte for the same shape;
- ~4.65 MiB fixed project/schema overhead in the fitted benchmark.

For each projected session, use the conservative estimate:

```text
session_estimate = max(
  searchable_message_count * 9789.7,
  searchable_text_bytes * 5.5839
)
```

For each shard candidate:

```text
shard_estimate = fixed_overhead + sum(session_estimate)
```

The estimate is a planning model, not a verification result. Real materialization must read back actual Postgres/project size before READY/promotion.

## Deterministic initial packing

For the initial accepted snapshot:

1. compute per-projected-session searchable message count and searchable text bytes for `dialogue|evidence|trace` only;
2. sort sessions by `first_accepted_at` ascending;
3. break ties deterministically with the private projected session ID;
4. append whole sessions to the current shard until adding the next session would exceed the working target of 700 MiB estimated Postgres size;
5. close that shard and continue with the next monotonic shard number.

All 426 current projected sessions have a non-null `first_accepted_at`, so no fallback ordering is needed for the present snapshot. A future allocator must define a deterministic fallback before accepting null chronology.

## Current accepted-snapshot allocation

Using only private-safe aggregate outputs, the current accepted snapshot packs into three candidate project shards:

| Shard | Sessions | Searchable messages | Searchable text bytes | Estimated Postgres | Hard-limit headroom |
| --- | ---: | ---: | ---: | ---: | ---: |
| `chatgpt-0001` | 37 | 42,191 | 117,164,455 | ~669.6 MiB | ~34.6% |
| `chatgpt-0002` | 134 | 67,361 | 117,239,318 | ~699.2 MiB | ~31.7% |
| `chatgpt-0003` | 255 | 57,745 | 61,685,305 | ~556.1 MiB | ~45.7% |

Totals:

- sessions: 426;
- searchable messages: 167,297;
- searchable text bytes: 296,089,078;
- conservative estimated Postgres footprint across the three projects: ~1.88 GiB.

The uneven message counts are expected. One session in the first candidate has an estimated footprint of ~184.3 MiB, demonstrating why fixed row-count shard limits are unsafe.

## Stable shard identity and future growth

Shard IDs are monotonic and provider-scoped:

```text
chatgpt-0001
chatgpt-0002
chatgpt-0003
...
```

Once an initial allocation is promoted, closed shard membership is persistent operational state. Future accepted sessions append to the current open shard until it reaches the closure threshold, then a new monotonic shard is opened.

Do not reshuffle old sessions merely to rebalance size. Silent reshuffling would make provenance, rollback, and cache invalidation harder to reason about.

If an already-assigned session grows enough to threaten a hard project limit, perform an explicit whole-session migration under a new catalog generation. That migration must have its own evidence, verification, and rollback path.

## Required shard provenance

Each materialized shard/catalog entry must bind at minimum:

- shard ID;
- provider/source class;
- project ID;
- generation ID;
- projection schema version;
- accepted-set/generation identity or equivalent accepted-evidence watermark;
- materializer/adapter version;
- session count;
- searchable message count;
- observed Postgres/project size;
- state: `BUILDING | READY | RETIRED`;
- optional time bounds used only as derived routing metadata.

Private session IDs and source identifiers stay out of public receipts.

## Blue/green materialization and promotion

On the Free plan, use separate-project blue/green candidates rather than assuming a near-full active project can hold a replacement full generation on a child branch.

For each candidate shard:

1. create a fresh project with the versioned shard schema/migrations;
2. materialize only the assigned projected sessions from accepted evidence;
3. keep generation state `BUILDING`;
4. read back actual session/message counts and actual Postgres/project size;
5. verify accepted-evidence provenance and projection derivation;
6. transition generation to `READY` only through the validated lifecycle function;
7. create/verify the least-privilege serving LOGIN role;
8. deploy the bounded shard Function;
9. run private bounded search/status acceptance against the candidate;
10. add the candidate to a **non-production catalog generation**;
11. verify full fan-out/merge and failure semantics without changing the production catalog;
12. only after all shards in the candidate catalog are READY and verified, atomically switch the router/catalog generation;
13. read back the exact active catalog/shard/generation identities through the serving path;
14. retain the old catalog/projects for a bounded rollback window;
15. retire/delete old projects only after explicit cleanup disposition.

## Failure semantics

Promotion is catalog-generation atomic.

If any candidate shard fails build, size, provenance, READY transition, credential, Function, or search acceptance:

- do not promote a partial candidate catalog;
- keep the current production catalog unchanged;
- report the candidate shard as failed/degraded;
- repair/rebuild only the failed candidate where possible;
- never infer source-evidence corruption from projection failure alone.

## Rollback

Rollback is a catalog-generation swap back to the previously verified project/generation set.

Rollback acceptance requires readback of:

- active catalog generation;
- expected project IDs;
- expected shard generation IDs;
- bounded search result from the restored path;
- no unexpected partial/unavailable shards.

Do not rebuild during an emergency rollback unless the previous verified projects are themselves unavailable.

## Pre-cutover gates

Before real-corpus materialization/cutover is authorized, a follow-up implementation issue must explicitly define and verify:

- current accepted-evidence snapshot/watermark to promote;
- private source acquisition availability;
- materializer implementation for the private assignment;
- actual size readback vs the planning model;
- current edge authentication policy;
- production catalog update mechanism and atomicity;
- rollback retention window and cleanup authority;
- current local fallback/runtime status.

## Current disposition

The allocation and promotion procedure are READY AS A PLAN.

Real corpus upload/materialization and production cutover remain **NOT AUTHORIZED** by #75.
