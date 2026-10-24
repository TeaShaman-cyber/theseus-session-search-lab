# Projection backend sizing checkpoint

Issue #61 compares the local SQLite/FTS projection with a remote Postgres/Neon derived projection while preserving the rule that accepted source evidence remains authority and both search projections are rebuildable state.

This checkpoint follows the verified federated serving work in #64. It does not authorize real-corpus migration or production cutover.

## Current private-safe corpus shape

Only aggregate measurements were used from the bound local projection; no message text, source identifiers, Drive identifiers, private URLs, or private digests were copied into the benchmark.

Observed local corpus aggregates:

- accepted artifacts: 737;
- sessions: 426;
- canonical messages: 257,744;
- searchable messages admitted by the Neon serving schema: 167,297;
- searchable classes:
  - dialogue: 149,021;
  - evidence: 4,083;
  - trace: 14,193;
- hidden messages: 90,447 and intentionally absent from the remote serving schema;
- searchable text bytes: 296,089,078;
- searchable text bytes/message average: 1,769.84;
- p50: 532 bytes;
- p90: 4,376 bytes;
- p95: 5,757 bytes;
- p99: 9,838 bytes;
- observed maximum: 4,831,830 bytes.

The current local SQLite/FTS file is 614,764,544 bytes, but that file also contains evidence/provenance/routing tables and hidden rows that are not part of the remote serving projection.

## Synthetic benchmark shape

A disposable Neon child branch and a local `/tmp` SQLite database were loaded with the same private-safe synthetic shape:

- 426 sessions;
- 75,000 searchable messages;
- dialogue/evidence/trace and role proportions approximating the aggregate corpus distribution;
- text-length buckets chosen to approximate the observed p50/p90/p95/p99 and average;
- deterministic md5-like lexemes separated by spaces so payloads are poorly compressible and FTS indexes see substantial lexical diversity rather than a trivial repeated token.

The synthetic lexical diversity is deliberately conservative for storage sizing. It is not a semantic relevance benchmark.

## Storage results

### Neon/Postgres 17

Database size progression on the disposable benchmark branch:

| Searchable messages | Database bytes | Approx MiB |
| ---: | ---: | ---: |
| 0 | 7,979,008 | 7.6 |
| 25,000 | 245,088,256 | 233.7 |
| 50,000 | 492,748,800 | 469.9 |
| 75,000 | 741,236,736 | 706.9 |

The observed slope is approximately 9.79 KB per searchable message for this shape.

At 75,000 rows, the `messages` relation family accounted for 733,257,728 bytes (~699.3 MiB):

- main heap: 104,407,040 bytes (~99.6 MiB);
- indexes: 393,928,704 bytes (~375.7 MiB);
- remaining relation storage, primarily TOAST/auxiliary data: ~224.0 MiB.

A linear extrapolation of the measured slope to the current 167,297 searchable-message shape is about 1.53 GiB. Therefore the current searchable corpus does not plausibly fit in one 1-GiB Neon Free project with safe growth headroom.

### SQLite/FTS5

The same 75,000-message synthetic shape produced a 324,173,824-byte SQLite file (~309.2 MiB).

Large components from `dbstat`:

- `messages`: 163,074,048 bytes;
- `messages_fts_data`: 147,021,824 bytes;
- message identity/order indexes together: ~9.9 MiB;
- FTS docsize/index/config and sessions: small relative to message/FTS data.

For the same synthetic shape, Neon/Postgres consumed about 2.29x the SQLite file size.

## Query observations

These timings isolate different layers and must not be collapsed into one latency claim.

### Database execution only

On the 75,000-row Neon branch, `EXPLAIN ANALYZE` around the bounded `search_generation()` function showed:

- first exact-token read observed in the benchmark: 27.544 ms, including 48 shared read blocks;
- repeated exact-token read: 0.339 ms;
- no-hit query: 0.307 ms.

This is SQL execution inside the database. It is **not** a scale-to-zero compute cold-start or public-network measurement.

For local in-process SQLite/FTS5 on the same synthetic shape:

- first exact-token lookup: ~0.323 ms;
- subsequent exact-token lookups: ~0.04-0.05 ms;
- no-hit lookups after warmup: ~0.04 ms.

SQLite therefore remains materially faster as a local read cache, while warm Postgres SQL remains sub-millisecond for the tested bounded lookup.

### Serving/network path

A separate six-run HTTP probe of the canonical two-shard Session Search router returned `FOUND` with two hits and no unavailable shards on every run.

Observed total request times:

- first observed run: 1.654 s;
- subsequent runs: 0.409, 0.256, 0.306, 0.229, 0.229 s;
- median of the subsequent five runs: ~0.256 s;
- observed TTFB range: ~0.152-0.163 s.

These measurements include public HTTP, Neon Functions, router fan-out, and two shard Functions, but use the tiny synthetic #64 shards rather than the 75k sizing branch. A controlled scale-to-zero cold-start measurement remains UNKNOWN.

## Decision checkpoint

The evidence now rejects a simple "replace the current SQLite file with one Neon project" design on the Free plan.

The leading architecture remains hybrid/federated:

```text
durable accepted source exports
  -> deterministic ingest / verification
  -> project-level Neon derived shards
  -> independent router / Arcade / ChatGPT serving

plus

local SQLite/FTS projection or cache
  -> low-latency local/offline workflows
```

### Initial shard closure policy

Shard closure should be driven by observed Postgres logical/database size, not raw export bytes, SQLite file size, or a fixed message count.

For the current ChatGPT searchable density, an initial operational target of roughly **700 MiB per active shard** corresponds to about **70-75k searchable messages** and leaves roughly 30% of the 1-GiB project limit unused for ordinary growth/variance.

At that target, the current ~167k searchable-message shape implies approximately three project shards. This row count is descriptive, not a permanent policy; other providers can have different text/index density.

### Rebuild/promotion implication

Neon branches are copy-on-write: a child initially shares parent data, but changed/new data adds project storage. The Free plan limit is 1 GiB of Postgres storage per project. Therefore a near-full active shard should not assume it can build a second full generation on a child branch within the same project's storage budget.

For Free-plan production operation, the safer blue/green pattern is:

```text
active shard project A
  -> build/verify replacement in separate project B
  -> catalog generation/project swap
  -> retain A for bounded rollback window
  -> later retire A
```

This preserves generation verification without requiring active+replacement copies to fit inside one project's 1-GiB budget.

## Still unknown / not authorized

This checkpoint does not establish:

- controlled scale-to-zero cold-start latency;
- real-corpus Postgres size, because no private corpus content was migrated;
- production shard boundaries by provider/time range;
- production authentication/cutover policy;
- production corpus promotion authorization.

Those remain separate postconditions before any real migration.
