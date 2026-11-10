# Backend-neutral exact-text grouping research

Coordination: issue #252, child of #188.

Status: frozen research/design record from #252. Its original ‘flat default / grouped opt-in’ statements document the pre-#264 state and are intentionally retained as historical reasoning. #264 subsequently accepted exact-text grouping as the normal Session Search result contract; see `docs/exact-text-grouping-contract.md` for the current contract.

## Question

Where should exact-text grouping live so that local SQLite retrieval, current
federated Postgres/Neon serving, and future lexical adapters share one rule:

> identical text consumes one result slot, while every historical candidate
> occurrence and its provenance remain recoverable.

The measured symptom is duplicate-slot pressure in bounded Top-K output. It is not
a claim that repeated historical events are duplicates. Accepted artifacts and
their event/branch identity remain authoritative.

Primary repository evidence:

- `docs/backend-neutral-candidate-contract.md`
- `docs/backend-neutral-lexical-seam.md`
- `docs/exact-text-grouping-contract.md`
- `session_search/search.py`
- `session_search/candidate_contract.py`
- `functions/candidate-contract.mjs`
- `functions/grouping.mjs`
- `functions/shard/core.mjs`
- `functions/shard/handler.mjs`
- `functions/router/core.mjs`
- `functions/router/handler.mjs`
- `sql/neon/008_retrieval_policy.sql`
- `sql/neon/009_exact_text_grouped_retrieval.sql`
- `receipts/188-exact-grouping-real-corpus.public.json`
- `receipts/188-bm25-exact-grouping-crosscheck.public.json`
- `receipts/188-serverless-grouped-v1-smoke.public.json`

No vendor capability claim is needed to choose the seam: the relevant current
behaviour is owned by this repository's code and contracts.

## Plain mechanism

Grouping is a selection rule, not a storage rule.

```text
ranked candidate occurrences
        |
        v
group equal exact text
        |
        v
one result slot per text
        |
        +----> keep every occurrence/provenance reference
```

The important placement rule is:

> apply grouping after the relevant ranking step but before any truncation that
> could let repeated text consume slots needed by distinct text.

That rule matters more than whether the implementation happens in Python,
JavaScript, or SQL.

## Current architecture

### Local SQLite path

The local recall path already separates a bounded candidate pool from final output:

```text
SQLite FTS5
  -> _recall_candidate_rows()
       per-session cap
       candidate budget = 3 * requested limit
  -> rerank_recall_rows()
  -> flat Top-K                         (current default)
       or
  -> group_exact_search_rows()
  -> grouped Top-K                      (opt-in reference)
```

`search_corpus_grouped_recall()` is therefore already a valid reference for the
desired ordering: rank first, group exact text, then select K.

### Federated serverless path

The live-shaped federated path is different:

```text
Postgres shard search_generation_v2()
  -> shard returns at most requested limit event hits
  -> router derives shard rank
  -> reciprocal-rank merge across shards
  -> global flat Top-K
```

The experimental SQL seam instead does:

```text
Postgres shard
  -> bounded 3*K event candidates
  -> exact-text grouping
  -> K groups with occurrences
```

but `functions/shard/handler.mjs` still calls the flat search builder, and the
router only knows how to merge flat `hits[]`. The grouped SQL seam is not wired
into production serving.

The accepted serverless smoke observed 30 candidates -> 10 groups containing 12
selected event occurrences, with no production state left behind.

## Candidate v1: sufficient payload, not sufficient ordering policy

`theseus.session-search-candidate.v1` already carries the fields needed for exact
grouping:

- exact `text`;
- `session_id` + `ordinal`;
- `search_class`;
- optional message/session/provider/shard/generation provenance;
- backend score plus explicit score orientation.

A bounded probe under #252 projected the existing local and serverless Candidate v1
fixtures and fed them to both the Python and JavaScript exact-group helpers. Both
grouped the two equal `needle` texts into one group with two preserved occurrences.

This proves **shape compatibility only**. It does not make raw SQLite and Postgres
scores comparable. The candidate contract explicitly forbids that inference.

The smallest interface therefore should not ask grouping to understand scores.
Grouping should consume an **already ordered sequence**. Array order is the ranking
input; exact grouping preserves the first occurrence as representative.

No Candidate v2 is required for this.

### The actual small contract gap

The current serving request conflates two different quantities:

- `result_limit`: how many final result slots the caller wants;
- `candidate_budget`: how many ranked occurrences may be considered before grouping.

Local recall and experimental grouped Postgres both currently use `3 * result_limit`,
but that factor is an observed policy, not a backend-neutral invariant.

The adapter seam should make `candidate_budget` explicit internally. The grouping
module should not choose it.

Conceptually:

```text
retrieve_ranked_candidates(
    query,
    filters,
    search_mode,
    candidate_budget
) -> ordered Candidate v1[]

group_exact_select(
    ordered_candidates,
    result_limit
) -> ExactTextGroup v1[]
```

A backend may optimize this interface internally, but it must conform to the same
observable grouping result.

## Three placement options

### A. Group only inside each backend

Examples: keep Python grouping for SQLite and SQL grouping for Postgres.

**Advantage:** grouping occurs early enough to prevent local Top-K saturation.

**Problems:**

- semantics are duplicated per backend;
- future adapters must rediscover the rule;
- equal text from different federated shards still needs a router-level merge;
- backend-specific implementation can drift from the common contract.

Disposition: **insufficient alone**.

### B. Group only in the router/common caller

Every adapter returns flat candidates; one common layer groups them.

**Advantage:** one implementation owns grouping semantics.

**Problem:** with the current serverless contract each shard has already truncated
to `result_limit` before the router sees the list. Grouping there is too late to
recover distinct candidates displaced inside the shard.

The alternative is to return the full `candidate_budget` from every shard. That is
semantically clean but increases network payload and router work; the cost has not
been measured.

Disposition: **correct only if adapters expose enough pre-truncation candidates;
not correct with the current shard contract**.

### C. Shared semantic module + grouping at every lossy truncation seam

Recommended.

The project owns one contract:

```text
ordered Candidate v1[]
  -> exact-text group/select
  -> ExactTextGroup v1[]
```

Each runtime may implement it where necessary:

- local SQLite: Python reference after local rerank;
- serverless shard: SQL or JS before shard-level lossy truncation;
- federated router: merge equal groups across shards before global Top-K.

The SQL implementation is then an optimized adapter implementation, not the
definition of the feature. It must be conformance-tested against the shared
reference fixture.

This keeps the interface deep: callers only need ordered candidates/groups and K;
backend syntax, score orientation and storage stay behind adapters.

It also follows the repository QA rule not to recreate a second Python federation
implementation merely for symmetry. Federated routing remains JavaScript-only.

Disposition: **recommended**.

## Federated group merge

A shard may return:

```json
{
  "text": "same",
  "representative": {"session_id": "a"},
  "occurrence_count": 2,
  "occurrences": [{"session_id": "a"}, {"session_id": "b"}]
}
```

The router already has an ordering rule based on each shard's returned rank.
For grouped serving it can rank shard groups by that same position, then merge
equal text again across shards:

```text
shard A: same -> [A1, A2]
shard B: same -> [B1]

router:  same -> [A1, A2, B1]
```

A bounded #252 synthetic probe merged two shard-group lists containing five total
occurrences. The result retained all five occurrences and merged the shared text
into one three-occurrence group.

The router must not deduplicate occurrences by `message_id`: the existing contract
already states that message IDs are not globally unique, and repeated candidate
occurrences may encode distinct branch/projection evidence.

## Recommended internal result contract

Keep the existing group shape from `docs/exact-text-grouping-contract.md`:

```json
{
  "text": "exact body",
  "representative": {
    "session_id": "best-ranked occurrence",
    "message_id": "...",
    "ordinal": 12
  },
  "occurrence_count": 2,
  "occurrences": [
    {
      "session_id": "...",
      "message_id": "...",
      "ordinal": 12,
      "search_class": "dialogue",
      "provider": "chatgpt",
      "shard_id": "chatgpt-0001",
      "generation_id": "..."
    }
  ]
}
```

Rules:

1. group identity is exact Unicode string equality only;
2. input sequence is already ranked;
3. group order is first-ranked occurrence order;
4. every admitted candidate occurrence appears in exactly one group;
5. representative is ranking/presentation context, never historical identity;
6. filters and generation checks happen before grouping;
7. `FOUND` / `NO_SIGNAL` / `PARTIAL` remain retrieval-envelope semantics;
8. grouping never converts derived retrieval state into source authority.

No new candidate schema is needed. If a schema identifier is added, add it to the
group/result envelope rather than revising Candidate v1.

## Public-interface migration

Do not silently change the meaning of existing event-shaped `hits[]`.

The current MCP contract exposes flat hits, and consumers may depend on one hit
representing one occurrence. Replacing those objects in place would be a semantic
breaking change even if field names remained similar.

The thinnest safe path is:

1. implement and verify grouped behaviour internally first;
2. keep current flat serving as the default;
3. expose grouped output only through an explicit additive opt-in after canary
   evidence exists;
4. decide later whether grouped output should become the default, using a separate
   migration/cutover decision.

Do not return both full flat hits and full groups by default: that doubles payload
and creates two competing interpretations of the same result.

## Search-mode boundary

Existing grouped evidence is recall-only:

- local `search_corpus_grouped_recall()`;
- serverless `search_generation_grouped_v1()`.

Therefore the first implementation slice should remain **recall-only**.

`strict` grouping is `UNKNOWN`: it has not been measured against an independent
holdout and should not be inferred from recall evidence.

`auto` currently executes strict first and only falls back to recall after clean
`NO_SIGNAL`. A grouped public result for `auto` would otherwise have two possible
shapes depending on which arm wins. Do not introduce that ambiguity in the first
slice.

## Thin reversible implementation slices

### Slice 1 — shared conformance contract, no serving change

- Keep Candidate v1 unchanged.
- Refactor Python/JS exact grouping tests so Candidate v1-shaped rows are explicit
  conformance inputs.
- Add a shared group-result fixture, including mixed provider/shard provenance.
- Add the invariant:
  `sum(group.occurrence_count) == input_candidate_count`.

Acceptance: existing flat behaviour unchanged; `tools/dev/check` green.

### Slice 2 — router group merge, still not wired

Add one pure JavaScript function beside `groupExactTextHits`, conceptually:

```text
mergeExactTextGroups(ranked_groups, result_limit)
```

It:

- receives groups already ordered by router ranking;
- keeps first-ranked representative;
- concatenates occurrences for exact-equal text;
- preserves deterministic ordering;
- returns at most K groups.

Tests must cover equal text across two shards/providers, deterministic ties,
same message ID with different text, and occurrence conservation.

Acceptance: pure tests only; no HTTP/MCP behaviour change.

### Slice 3 — shard grouped canary path

Reuse the existing grouped Postgres seam or an equivalent JS implementation,
but make the path explicitly opt-in. Do not change the default flat builder.

The internal query contract should separate `candidate_budget` from
`result_limit`; initially preserve the already-measured recall policy
`candidate_budget = min(300, 3 * result_limit)` for parity rather than retuning it.

Acceptance: shard grouped output conforms to the shared fixture and generation
pinning/filter semantics remain identical.

### Slice 4 — federated grouped canary

Wire the opt-in grouped shard response into the router group merge.

Preserve:

- selected shard filtering;
- READY checks;
- generation pinning;
- provider/shard readback;
- `PARTIAL` and unavailable-shard reasons;
- request receipt provenance.

Add explicit grouped metrics rather than overloading flat-hit names, for example:

- candidate occurrence count;
- candidate group count;
- returned group count.

Acceptance: same frozen queries can run flat and grouped side by side with the
same shard release vector.

### Slice 5 — live comparison, still no default cutover

Compare flat vs grouped on a frozen query set plus bounded real canary traffic.

Measure:

- unique text per K;
- target R@K / MRR on the frozen set;
- candidate occurrence conservation;
- response bytes;
- router/shard latency;
- regressions and `PARTIAL` behaviour.

Only after this evidence should a public grouped result opt-in or default migration
be proposed.

## QA matrix

Deterministic tests should cover:

| Invariant | Test |
| --- | --- |
| candidate occurrences are never deleted | sum occurrences before/after grouping |
| exact text only | near-equal/normalized text stays separate |
| message ID is not identity | same ID + different body stays separate |
| cross-shard duplicates | one group, occurrences from both shards |
| cross-provider duplicates | one group, provider provenance retained |
| deterministic ordering | tie fixture repeated byte-for-byte |
| candidate budget before Top-K | duplicate saturation fixture recovers distinct text |
| session/time filters | excluded candidates never reappear through grouping |
| generation pinning | stale/mismatched shard rejected before merge |
| PARTIAL | successful groups returned with unavailable shard evidence |
| flat compatibility | default `hits[]` path unchanged |
| score boundary | no mixed-backend raw-score sort |
| cross-runtime semantics | Python/JS/SQL conformance fixture |

Run the repository's canonical `./tools/dev/check` before model review or promotion.
A live SQL smoke is required only when SQL behaviour itself changes.

## Five Whys

1. Why can duplicate text consume Top-K slots?
   Because ranking operates on event rows, and repeated branch/session occurrences
   are separate rows.
2. Why not delete those rows?
   Because they are historical/provenance evidence, not redundant authority.
3. Why not group only after the final response?
   Because distinct candidates may already have been discarded by a prior Top-K.
4. Why not make grouping a Postgres feature?
   Because SQLite already needs the same semantics and future adapters will too.
5. Smallest evidenced design:
   own exact grouping as a backend-neutral ranked-candidate contract and apply it
   before each lossy truncation seam, with adapter-specific optimized implementations
   checked against common conformance evidence.

Stop here. A new backend, semantic deduplication, score normalization, MMR, or a
query-planner redesign is not required to implement this invariant.

## Disposition

`IMPLEMENTABLE_NOW / CANDIDATE_V1_SUFFICIENT / EXPLICIT_CANDIDATE_BUDGET_NEEDED / HYBRID_SEAM_RECOMMENDED / RECALL_ONLY_FIRST_SLICE / NO_PRODUCTION_CUTOVER`

FACT:
- both local and serverless grouped reference implementations already exist;
- Candidate v1 preserves the required text/identity/provenance fields;
- current router cannot merge grouped shard results;
- current live-shaped shard contract truncates flat hits before router grouping;
- existing grouped evidence is recall-only.

INFERENCE:
- the smallest durable architecture is a shared exact-group semantic contract with
  conformance implementations at local/shard truncation seams plus a router group
  merge for federation.

UNKNOWN:
- payload/latency cost of returning raw `candidate_budget` rows to the router versus
  grouping inside each shard;
- whether strict-mode grouping improves user-visible quality;
- whether grouped output should ever replace flat event-shaped output by default.

The next concrete step, if implementation is authorized under this issue, is
**Slice 1 + Slice 2 only**: strengthen the Candidate v1/group conformance fixture and
add a pure router group-merge function with no serving-path change.
