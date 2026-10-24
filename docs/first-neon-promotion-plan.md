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

The current corpus routes accepted artifacts to `projected_session_id` through `artifact_routes`. The projected session remains the atomic packing unit and is never split across project shards.

Provider lineage is an outer routing boundary. Allocation is performed independently per provider family before session packing. Current explicit adapter lineage is:

- `chatgpt-export`, `barn-doctor`, `barn-doctor-recovery`, `speed-booster-export` -> `chatgpt`;
- `deepseek-export` -> `deepseek`;
- `xai-export` -> `xai` when present;
- unmapped adapters do not imply a provider.

An unknown adapter may inherit a provider only when its projected session is co-routed with exactly one known provider. Multiple different known providers in one projected session are a provenance conflict and fail closed. An unknown-only projected session is `UNRESOLVED_PROVIDER` and receives no production shard ID.

If a future schema adds an explicit routing/conversation-family identity stronger than projected-session identity, the allocator must preserve that stronger atom instead of silently splitting a family.

## Sizing model

The #61 benchmark observed approximately:

- 9,789.7 Postgres bytes per searchable message for the representative synthetic shape;
- 5.5839 Postgres bytes per searchable text byte for the same shape;
- ~4.65 MiB fixed project/schema overhead in the fitted benchmark.

The executable allocator encodes those coefficients as integer ratios (`97897/10` and `55839/10000`) so repeated planning does not depend on floating-point rounding.

For each projected session:

```text
session_estimate = max(
  ceil(searchable_message_count * 97897 / 10),
  ceil(searchable_text_bytes * 55839 / 10000)
)
```

For each shard candidate:

```text
shard_estimate = fixed_overhead + sum(session_estimate)
```

The working target remains **700 MiB = 734,003,200 bytes**. The current official Neon Free hard project limit is **1 GB = 1,000,000,000 bytes**; headroom calculations use that decimal hard limit rather than 1 GiB.

The estimate is a planning model, not verification. Real materialization must read back actual Postgres/project size before READY/promotion, and provider-specific real-data density may differ from the conservative mixed-corpus model.

## Deterministic initial packing

For a given accepted projection:

1. derive provider from routed adapter lineage using the fail-closed rules above;
2. leave unknown-only sessions unresolved rather than assigning a guessed shard family;
3. within each resolved provider, compute per-session searchable message count and searchable UTF-8 text bytes for `dialogue|evidence|trace` rows;
4. sort by `first_accepted_at` ascending;
5. break ties deterministically with the private projected session ID;
6. append whole sessions to the current provider shard until adding the next session would exceed the 700 MiB working target;
7. close that shard and continue with the next monotonic provider-scoped shard number;
8. if one atomic session exceeds the working target but remains below the 1 GB hard limit, isolate it in its own over-target shard and require explicit size acceptance;
9. if one atomic session itself exceeds the hard limit, fail closed rather than splitting it implicitly.

The public CLI output contains only aggregate counts/sizes. Private session-to-shard membership remains in-process operational state and is not printed in public receipts.

## Current accepted-snapshot allocation

A provider-aware read-only run against the current accepted projection yields:

| Shard | Sessions | Searchable messages | Searchable text bytes | Estimated Postgres | 1 GB headroom |
| --- | ---: | ---: | ---: | ---: | ---: |
| `chatgpt-0001` | 136 | 47,156 | 123,811,905 | ~696.7 MiB | 26.9% |
| `chatgpt-0002` | 246 | 55,161 | 57,438,642 | ~531.9 MiB | 44.2% |
| `deepseek-0001` | 43 | 64,662 | 114,332,170 | ~693.3 MiB | 27.3% |

Resolved totals:

- sessions: 425;
- searchable messages: 166,979;
- searchable text bytes: 295,582,717.

One additional `third-party-dialogue-bridge`-only projected session is `UNRESOLVED_PROVIDER` and is deliberately not assigned to a production shard:

- sessions: 1;
- searchable messages: 318;
- searchable text bytes: 506,361.

Resolved + unresolved totals reconcile to the accepted projection totals of 426 sessions, 167,297 searchable rows, and 296,089,078 searchable text bytes.

This supersedes the candidate allocation in `receipts/075-first-neon-promotion-plan.public.json`, which incorrectly treated the mixed-provider corpus as one ChatGPT shard family and reported headroom against 1 GiB. The historical #75 receipt is retained unchanged as point-in-time evidence; the corrected current allocation is recorded in the #77 receipt.

## Stable shard identity and future growth

Shard IDs are monotonic and provider-scoped:

```text
chatgpt-0001
chatgpt-0002
...
deepseek-0001
deepseek-0002
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

The promotion procedure remains READY AS A PLAN, but its current allocation is the provider-aware #77 contract above.

`session_search/shard_allocation.py` and `scripts/plan_neon_shards.py` are the executable planning contract. Public output is aggregate-only and unknown-only provider lineage remains unresolved.

Real corpus upload/materialization and production cutover remain **NOT AUTHORIZED** by this plan or #77.
