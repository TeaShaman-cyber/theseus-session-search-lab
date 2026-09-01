# Multi-Session Corpus Ingest Design

Date: 2026-09-01
Repository: `TeaShaman-cyber/theseus-session-search-lab`
Tracking issue: #7
Status: design for review; not implemented

## 1. Purpose

Session Search currently proves that one portable capture can be verified, normalized, projected into SQLite FTS5, and searched. The current importer is intentionally destructive: it deletes the target database and rebuilds a one-session projection from one capture artifact.

The next step is a cumulative corpus that can ingest many independent chats and repeated captures of the same chat without destructive manual rebuilds.

The user-facing goal is deliberately small:

```text
new portable capture(s)
        ↓
one ingest command
        ↓
all accepted sessions become searchable together
```

The architecture must preserve the existing project invariant:

> Session history is evidence. Search indexes are projections, not authority.

## 2. Scope

Version 1 provides:

- one corpus containing many sessions;
- one-command ingest of one or more portable capture artifacts;
- idempotency by artifact SHA-256;
- repeated captures of the same stable session;
- message deduplication scoped by stable session identity;
- hard rejection of conflicting duplicate message IDs within one session;
- artifact → page → message provenance;
- conservative per-session coverage metadata;
- transactional database updates;
- a private content-addressed artifact store;
- rebuild of the SQLite/FTS projection from immutable artifacts;
- corpus-aware search results carrying session and coverage metadata;
- receipts for applied, no-op, blocked, and failed ingest operations;
- a durable accepted-artifact membership ledger separate from the disposable SQLite projection;
- one-writer mutation locking for ingest/rebuild operations;
- a configured default corpus for seamless assistant operation without hiding explicit path control;
- compatibility with future automated transport/index refresh in issue #3.

## 3. Non-goals

Version 1 does not:

- make SQLite or FTS an authority;
- turn Session Search into semantic memory;
- depend on Barn Doctor or Google Drive;
- infer that a search miss means historical absence unless coverage independently supports that claim;
- infer complete coverage by heuristically stitching arbitrary partial captures;
- deduplicate anonymous messages across independent artifacts;
- solve browserless capture acquisition;
- automatically watch Google Drive; that remains issue #3;
- publish raw conversation content, provider IDs, Drive IDs, cursors, or private artifact hashes in public repository records.

## 4. Alternatives considered

### 4.1 Append directly into the current single SQLite file

This is the smallest code change, but it makes the projection progressively look like the durable record. Recovery, rebuild, and provenance become harder to reason about.

Rejected as the primary architecture.

### 4.2 Keep one SQLite database per session and federate search

This isolates failures well but makes global ranking, repeated-capture deduplication, corpus verification, and assistant use unnecessarily awkward.

Rejected for normal operation. Per-session databases remain useful as scratch/debug projections.

### 4.3 Immutable artifacts plus one regeneratable corpus projection

Recommended and approved direction.

```text
portable captures
      ↓ verify + SHA-256
private content-addressed artifact store
      ↓ normalize / validate
transactional corpus ingest
      ↓
corpus relational model + FTS5 projection
      ↓
one search surface across all sessions
```

This keeps the evidence/projection boundary explicit while making daily use simple.

## 5. Corpus layout

A corpus is a directory, not merely a database file:

```text
corpus/
├── artifacts/
│   └── sha256/
│       └── <sha256>.zip
├── ledger/
│   └── accepted/
│       └── <sha256>.json
├── receipts/
│   ├── ingest/
│   │   └── <sha256>-<attempt>.json
│   └── rebuild/
│       └── <timestamp-or-generation>.json
├── staging/
├── mutation.lock/
└── corpus.sqlite3
```

`artifacts/`, `ledger/`, and `receipts/` are durable private evidence/audit state. `corpus.sqlite3` is disposable and rebuildable. The presence of an artifact blob alone does **not** make it a corpus member. Rebuild eligibility comes only from a verified accepted-ledger entry.

`mutation.lock/` exists only while a mutating operation owns the corpus writer lock; it is not durable corpus membership state.

The public Git repository contains code, synthetic fixtures, contracts, and sanitized receipts only. Real corpus directories stay private.

## 6. Stable identity rules

### 6.1 Artifact identity

The artifact identity is the SHA-256 of the exact portable artifact bytes.

A source filename, Drive path, upload timestamp, or transport identifier is metadata, not identity.

If the artifact SHA-256 is already present in both the accepted-artifact ledger and the current projection, ingest returns an idempotent no-op receipt and changes no searchable state. A blob that merely exists under `artifacts/sha256/` without an accepted-ledger entry is unaccepted staging evidence, not an ingested corpus member.

### 6.1.1 Accepted-artifact membership ledger

The durable accepted-artifact ledger is the corpus-membership record used by rebuild. It is **not** authority over what happened in the historical conversation; the capture artifact remains the historical evidence. The ledger only records that a verified artifact has been accepted as an input to this corpus.

Each immutable `ledger/accepted/<sha256>.json` entry contains at minimum:

- ledger schema version;
- exact artifact SHA-256 and size;
- source schema/adapter identifier;
- resolved stable session identity;
- artifact coverage state;
- message canonicalization contract version;
- durable `accepted_at`;
- enough normalized counts to support readback/reconciliation checks.

The filename SHA, embedded SHA, and stored artifact bytes must agree. Accepted-ledger entries are immutable in v1; correction means producing an explicit new reconciliation/migration record rather than silently editing membership history.

### 6.2 Session identity

Cumulative ingest requires a stable session identity from the source adapter contract.

For the current Barn Doctor adapter:

- the conversation-detail payload supplies `conversation_id`;
- paginated conversation-message payloads without their own conversation ID inherit that verified artifact-level session identity;
- every conversation payload in one artifact must resolve to the same session identity.

Blocked states:

- `BLOCKED_UNRESOLVED_SESSION_ID` when a stable session identity cannot be resolved;
- `BLOCKED_MIXED_SESSION_ARTIFACT` when one artifact contains payloads resolving to different stable session identities.

The cumulative corpus must never silently substitute a generic ID such as `session`.

### 6.3 Message identity

For messages with a provider message ID, deduplication identity is:

```text
(session_id, message_id)
```

The same provider message ID in two different sessions is allowed.

Each identified message carries two distinct digests:

- `source_object_sha256`: SHA-256 of the complete source message object after deterministic JSON serialization; this is forensic provenance and may differ across captures because provider/UI metadata can change;
- `canonical_message_sha256`: SHA-256 of a deliberately narrow normalized semantic object used for dedup/conflict decisions.

For v1, the canonical semantic object contains exactly:

```text
author.role
content.content_type
normalized semantic content payload
```

The normalized semantic content payload is the content value used by the adapter to preserve the message's actual semantic payload (for example text parts, code text, or equivalent structured content). Provider/UI metadata outside `author.role` and `content` is excluded. Provider message ID is the dedup key and is not duplicated inside the digest. Create time remains stored/provenanced metadata but does not by itself create a hard content conflict.

The adapter must implement canonicalization as a versioned function covered by fixtures. Changing the canonicalization contract requires a corpus schema/adapter version decision rather than a silent code change.

Rules for a repeated `(session_id, message_id)`:

- same canonical digest, same or different source-object digest → reuse the existing canonical message row and add provenance;
- different canonical digest → hard failure and rollback with `CONFLICTING_DUPLICATE_MESSAGE_ID`.

`message_sources` preserves the per-occurrence `source_object_sha256`, so harmless provider-metadata variants remain auditable without duplicating searchable content.

Messages without a provider message ID are not deduplicated across artifacts in v1. They receive artifact/page-position-derived local identity. False duplicates are preferable to false merges.

## 7. Relational model

Version 1 introduces a corpus schema version distinct from the existing single-import projection.

### 7.1 `corpus_meta`

Stores projection schema/version metadata, for example:

- `schema_version`;
- projection generation identifier;
- build tool version when useful.

### 7.2 `artifacts`

One row per accepted content-addressed artifact:

- `artifact_id` integer primary key;
- `sha256` text unique;
- `size_bytes`;
- `source_schema`;
- `source_adapter`;
- `original_filename` as non-authoritative metadata;
- durable `accepted_at` copied from the accepted-ledger entry;
- accepted-ledger entry digest or equivalent readback link;
- artifact-level coverage state;
- resolved `session_id`.

### 7.3 `sessions`

One row per stable session:

- `session_id` primary key;
- current observed title;
- aggregate `coverage_state`;
- `coverage_reason`;
- first/last observed message time;
- first/last artifact ingest metadata as convenience fields.

Title is descriptive metadata, not identity.

### 7.4 `payload_pages`

Extends the existing page-provenance model with:

- `artifact_id` foreign key;
- `session_id` foreign key;
- capture sequence;
- member name;
- start/end cursors when available;
- previous/next page flags;
- message count;
- page time bounds.

Uniqueness is scoped as:

```text
(artifact_id, member_name)
```

A member filename is not globally unique across captures.

### 7.5 `messages`

One canonical row per deduplicated identified message, plus independent rows for anonymous occurrences:

- `row_id` integer primary key;
- `session_id` foreign key;
- nullable `message_id`;
- `canonical_message_sha256`;
- role;
- content type;
- search class;
- create time;
- normalized searchable text;
- per-session ordinal as convenience metadata.

When an ingest adds earlier/later messages to an existing session, ordinals for that touched session may be recomputed deterministically. `row_id` remains the internal FTS/provenance key.

### 7.6 `message_sources`

Many-to-many provenance between canonical message rows and captured payload pages:

- `message_row_id`;
- `page_id`;
- page position;
- source message ID where present;
- `source_object_sha256` for that captured occurrence.

Repeated captures of the same canonical message therefore increase provenance without duplicating searchable content, while raw source-object variants remain auditable.

### 7.7 `messages_fts`

FTS is projection-only. It indexes searchable message text and uses `row_id` to join current metadata from `messages` and `sessions`.

Metadata such as ordinal, coverage, title, and role should not be copied into FTS merely for convenience when it can be joined from relational tables. This reduces synchronization surface during incremental ingest.

## 8. Ingest operation

The normal command is designed for both humans and future automation:

```bash
python3 -m session_search.corpus ingest capture-a.zip capture-b.zip --corpus <corpus-root>
```

For seamless repeated use, `--corpus` may be omitted when `SESSION_SEARCH_CORPUS` is set. Resolution order is explicit and deterministic:

```text
1. --corpus <path>
2. SESSION_SEARCH_CORPUS
3. error: corpus location unresolved
```

There is no hidden platform-specific default directory in v1. Explicit `--corpus` always overrides the environment setting. `ingest` creates the corpus on first use.

Each artifact is processed independently so one blocked artifact does not invalidate earlier successfully committed artifacts in the same invocation. The command returns a combined machine-readable summary plus one durable receipt per artifact.

### 8.1 Per-artifact flow

```text
1. resolve corpus location
2. acquire the exclusive corpus mutation lock
3. read source artifact
4. safety-check ZIP members
5. verify manifest sizes/hashes
6. compute exact artifact SHA-256
7. resolve one stable session identity
8. normalize pages/messages in memory
9. inspect corpus for artifact/message conflicts
10. copy artifact into staging
11. atomically rename to content-addressed artifact path
12. open SQLite transaction
13. insert artifact/page/session/provenance rows
14. insert only novel canonical messages + FTS rows
15. recompute touched-session metadata/coverage
16. run transaction-local invariants
17. keep the SQLite transaction open and uncommitted
18. atomically write accepted-ledger entry
19. read back and verify the accepted-ledger entry
20. commit SQLite transaction
21. read back ledger + database postconditions
22. atomically write ingest receipt
23. release corpus mutation lock
```

Artifact storage precedes database commit. If the database transaction later fails, the content-addressed artifact may remain as an unaccepted safe blob; a retry can reuse it. The database must never commit a reference to a missing artifact.

Filesystem membership and SQLite projection cannot be committed as one atomic transaction, so v1 deliberately writes and verifies the accepted-ledger entry **before** committing the already-validated SQLite transaction. Until the SQLite commit, existing readers continue to see the previous committed corpus state. This prevents an unaccepted artifact from becoming searchable.

A crash after accepted-ledger write but before SQLite commit can leave `LEDGER_ACCEPTED_PROJECTION_MISSING`. That state is accepted corpus membership with a stale projection, not successful ingest completion. `verify` must detect it; the next mutation must reconcile by rebuilding the disposable projection from accepted-ledger membership before accepting further mutations. If ledger creation fails, the still-open SQLite transaction rolls back and the artifact remains an unaccepted blob. No automatic guess may promote a blob into accepted membership.

### 8.2 Idempotent retry

If the artifact SHA has a valid accepted-ledger entry and matching current projection membership:

```text
status = ALREADY_INGESTED
mutation = none
```

The stored artifact bytes, accepted-ledger entry, registry row, and corpus counts are verified before reporting the no-op as successful. If ledger and projection disagree, ingest stops with `RECONCILIATION_REQUIRED` rather than pretending the retry is idempotent.

## 9. Transaction and failure semantics

An individual artifact ingest is atomic with respect to searchable corpus state.

Any of the following causes rollback for that artifact:

- corrupt ZIP or unsafe member path;
- manifest integrity mismatch;
- unsupported payload shape;
- unresolved or mixed session identity;
- conflicting duplicate identified message;
- database constraint failure;
- FTS insertion failure;
- post-insert invariant failure.

A failed artifact receipt records the stage and stable error class without publishing private message text or private identifiers.

There is no blind retry loop. A retry is a new explicit ingest attempt after the failure condition is understood or changed.

### 9.1 Corpus mutation lock

`ingest` and `rebuild` are exclusive corpus mutations and must serialize through a corpus-level writer lock. Search remains read-only and does not acquire the mutation lock.

The v1 lock is a portable atomic lock-directory creation at `<corpus-root>/mutation.lock/`, containing owner metadata sufficient for diagnosis (operation, process ID where available, host/runtime marker, start time). If the lock already exists, a second mutation fails fast with `CORPUS_MUTATION_LOCKED`.

A stale-looking lock is never broken automatically. Recovery is an explicit operator action after verifying that no writer is active, because silent stale-lock removal could create two writers. The implementation plan must include a safe diagnostic/recovery command or documented procedure.

SQLite transaction semantics remain the final guard for database consistency, but the corpus lock protects the wider filesystem + ledger + receipt mutation boundary that SQLite alone cannot serialize.

## 10. Coverage semantics

Coverage is always scoped to a session, never the whole corpus.

Artifact-level coverage keeps the existing meanings:

- `PARTIAL_SESSION_SLICE`;
- `COMPLETE_EXPOSED_CONVERSATION`.

### 10.1 Conservative aggregate rule for v1

Session aggregate coverage is `COMPLETE_EXPOSED_CONVERSATION` only when a single accepted artifact for that session explicitly proves complete exposed coverage and no other accepted artifact contributes messages outside that complete artifact's observed message set/time extent.

If a later or otherwise external partial capture adds new session messages beyond the known complete artifact, aggregate session coverage becomes `PARTIAL_SESSION_SLICE` until a newer complete artifact explicitly covers the expanded session.

Version 1 does **not** infer completeness by stitching multiple partial captures using timestamps alone.

Future work may prove a connected complete chain using explicit cursor/page overlap evidence. That is deliberately outside v1.

This rule prefers false incompleteness over false claims of historical completeness.

## 11. Search interface

Normal corpus search:

```bash
python3 -m session_search.search "Needle verbalizer" --corpus <corpus-root>
```

When `SESSION_SEARCH_CORPUS` is set, the normal assistant happy path may omit the explicit path:

```bash
python3 -m session_search.search "Needle verbalizer"
```

Search uses the same location precedence as ingest: explicit `--corpus` first, then `SESSION_SEARCH_CORPUS`, otherwise an unresolved-location error.

For backward compatibility, the existing `--db` path remains supported for legacy/scratch single projections.

Default corpus search spans all sessions and the existing default scopes (`dialogue`, `evidence`).

Each result exposes enough provenance for the assistant to reason about evidence quality:

- session identity;
- session title when present;
- session coverage state;
- message time;
- role;
- search class;
- message ID when present;
- score;
- text.

Optional filters:

```text
--session <stable-session-id>
--scope dialogue|evidence|trace
--limit N
```

Public output/receipts must remain sanitized. Private local search may expose private session identifiers because they are required for provenance and follow-up retrieval.

## 12. Verify and rebuild

### 12.1 Verify

```bash
python3 -m session_search.corpus verify --corpus <corpus-root>
```

Checks at minimum:

- SQLite `PRAGMA integrity_check`;
- every accepted-ledger artifact exists at its expected content-addressed path;
- every accepted-ledger artifact byte hash matches its membership record;
- accepted ledger and SQLite artifact registry have one-to-one membership;
- ledger-accepted/projection-missing or any impossible projection-without-ledger state is reported as reconciliation-required, not success;
- no unaccepted artifact blob is treated as rebuild-eligible solely because it exists on disk;
- message uniqueness invariants hold;
- page/source foreign-key relationships hold;
- FTS row coverage matches searchable canonical messages;
- stored per-session coverage summaries can be recomputed from artifact/page evidence.

### 12.2 Rebuild

```bash
python3 -m session_search.corpus rebuild --corpus <corpus-root>
```

Rebuild procedure:

1. acquire the exclusive corpus mutation lock;
2. enumerate only artifacts named by verified accepted-ledger entries;
3. validate ledger entries and artifact bytes independently;
4. create `corpus.sqlite3.new` from an empty versioned schema;
5. replay accepted artifacts in deterministic SHA-256 order;
6. derive session metadata with order-independent rules;
7. run full verify;
8. compare semantic corpus invariants with the previous projection when one exists;
9. atomically replace `corpus.sqlite3` only after verification succeeds;
10. emit a rebuild receipt;
11. release the mutation lock.

The old projection remains untouched on rebuild failure. A platform that cannot replace the projection while a reader has the old database open must return a stable swap-blocked error and leave both the old verified projection and the verified `.new` candidate intact; v1 never deletes the old projection first.

Deterministic rebuild equality applies to semantic/provenance state: sessions, canonical messages, source provenance, coverage, and searchable results. Runtime verification timestamps need not be byte-identical. Derived metadata must use explicit source-evidence rules rather than ingest order. In v1:

- session title is selected from the accepted artifact with the greatest observed source message time; artifacts with no observed message time sort before timed artifacts, and ties use lexicographically smallest artifact SHA-256;
- first/last observed message time are min/max over canonical message evidence;
- first/last accepted-artifact metadata use the durable accepted-ledger timestamps, not replay execution time;
- operational `last_verified_at`-style fields, if present, are excluded from semantic rebuild equivalence.

## 13. Receipts

Each ingest receipt contains private operational identifiers locally and supports a sanitized public projection.

Private receipt fields include:

- receipt schema version;
- artifact SHA-256;
- operation status;
- resolved session identity;
- artifact coverage state;
- pre/post corpus counts;
- novel/reused message counts;
- provenance additions;
- conflict/block reason when applicable;
- SQLite integrity result;
- corpus schema version.

Expected statuses include:

- `INGESTED`;
- `ALREADY_INGESTED`;
- `BLOCKED_UNRESOLVED_SESSION_ID`;
- `BLOCKED_MIXED_SESSION_ARTIFACT`;
- `FAILED_INTEGRITY`;
- `FAILED_CONFLICTING_DUPLICATE`;
- `FAILED_TRANSACTION`;
- `CORPUS_MUTATION_LOCKED`;
- `RECONCILIATION_REQUIRED`;
- `REBUILD_SWAP_BLOCKED`.

Receipt existence alone does not prove corpus mutation. Successful mutation claims require database readback/postcondition verification.

## 14. Migration and compatibility

The current one-artifact importer remains available as a portable scratch/debug primitive and as a normalization implementation source.

The corpus layer should reuse/refactor its validation and normalization logic rather than duplicate it.

Migration of the current private corpus is performed through source artifacts, not by declaring the old SQLite projection authoritative. Where an original verified artifact is available, it is ingested into the new corpus and receives a durable accepted-ledger membership record only after verified ingest postconditions. Legacy projection contents are used only to compare acceptance counts/search behavior.

If a historical projection cannot be traced to a retained source artifact, its rows are not silently promoted into the new artifact-backed corpus. Such data requires an explicit migration artifact/receipt or remains legacy evidence.

## 15. Relationship to other issues

- **#2** remains authority for the portable capture/session-artifact boundary. #7 extends it with cumulative corpus ingest semantics.
- **#3** should consume the #7 ingest primitive for automatic transport/index refresh. Drive notification logic must not own corpus semantics.
- **#6** improves Barn Doctor acquisition/durability. #7 accepts partial or complete artifacts from it but does not depend on Barn Doctor.
- Browserless authoritative history work in **#4** can later implement another capture adapter without changing corpus semantics.

## 16. Acceptance tests

### 16.1 Synthetic tests committed to the repository

The implementation must test:

1. two different session artifacts coexist in one corpus;
2. re-ingesting the same artifact SHA is a verified no-op;
3. a later artifact for the same session adds only novel messages and additional provenance;
4. same `(session_id, message_id)` + same canonical digest deduplicates;
5. same `(session_id, message_id)` + different canonical digest fails atomically;
6. same provider `message_id` in different sessions does not collide;
7. anonymous messages from different artifacts are not falsely merged;
8. incomplete artifact remains partial;
9. explicit complete artifact can promote session coverage;
10. later novel tail can conservatively demote aggregate coverage to partial;
11. failed ingest leaves prior corpus counts/search results unchanged;
12. `verify` detects a missing/tampered registered artifact;
13. `rebuild` recreates equivalent session/message/provenance/search invariants;
14. global search returns hits from multiple sessions with session coverage metadata;
15. session filter restricts search without changing ranking semantics inside the session;
16. an artifact blob left on disk after failed ingest but lacking accepted-ledger membership is excluded from rebuild;
17. accepted-ledger/database membership disagreement is detected as reconciliation-required, including crash recovery where ledger membership exists but the projection commit is missing;
18. same identified message with changed irrelevant provider metadata but identical canonical semantic payload deduplicates while preserving distinct source-object digests;
19. changed canonical semantic payload for the same `(session_id, message_id)` hard-fails atomically;
20. concurrent ingest/rebuild mutation attempts are serialized by the corpus writer lock and never produce two active writers;
21. rebuild after a different ingest/discovery order reproduces semantic/provenance invariants and deterministic title/time metadata;
22. `--corpus` overrides `SESSION_SEARCH_CORPUS`, while the environment setting enables the no-path happy path.

### 16.2 Private real-world acceptance cases

No raw content or private identifiers are committed, but the private verification suite currently has three distinct evidence shapes:

```text
case A: complete exposed session, 2034 messages
case B: distinct partial session slice, 434 messages
case C: distinct complete exposed session, 1321 messages across 10 payload pages
```

Observed overlap between A/B/C message-ID sets is zero.

Initial end-to-end acceptance target after implementation:

```text
3 sessions
3789 canonical messages before any within-session repeated-capture dedup
all three searchable from one corpus
case A coverage preserved complete
case B preserved partial
case C preserved complete
SQLite integrity = ok
artifact provenance retained
rebuild reproduces corpus invariants
```

The count is an acceptance fixture for these verified private artifacts, not a public dataset contract.

## 17. User experience target

Manual use should require no database surgery:

```text
1. place/export a portable capture
2. with `SESSION_SEARCH_CORPUS` configured once, run one corpus ingest command (or let #3 do it later)
3. search the same corpus without repeating its path
```

For assistant operation, the intended happy path is eventually:

```text
new Drive artifact detected
        ↓
download to staging
        ↓
corpus ingest
        ↓
verified receipt
        ↓
search immediately sees the new session
```

Transport remains replaceable. The corpus ingest command is the stable operational boundary.

## 18. Implementation boundary

This design does not authorize implementation until reviewed and approved.

After approval, the implementation plan should prefer a small corpus module that composes existing importer normalization functions, with TDD covering synthetic multi-session/idempotency/conflict/rebuild cases before production code changes. The plan must preserve the accepted-ledger boundary, versioned message canonicalization, deterministic rebuild metadata, explicit/default corpus resolution, and exclusive mutation locking defined above.
