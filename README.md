# Theseus Session Search Lab

Theseus Session Search Lab is a public research line under the [Theseus public-interest research program](https://github.com/TeaShaman-cyber/theseus-research). This repository does not define the Theseus program contract.

**Session history is evidence, not semantic memory. Search indexes are projections, not authority.**

The lab studies portable, verifiable historical session retrieval for assistants while keeping capture sources, transports, browsers, and development environments replaceable.

## Runtime boundary

```text
replaceable capture adapter
  -> replaceable transport/storage adapter
  -> portable session artifact
  -> importer/normalizer
  -> regeneratable search projection
  -> assistant session_search
```

The first development prototype used Barn Doctor, Google Drive, and MarcoPolo. **MarcoPolo is not a runtime dependency.** Barn Doctor and Google Drive are current adapter choices, not architectural requirements.

A deterministic standalone Python runtime can be built with `python3 scripts/build_portable_runtime.py ...`; its contract is documented in [Portable runtime](docs/portable-runtime.md). Hosted acceptance builds the ZIP in one GitHub Actions job and consumes it in a fresh job that does not checkout this repository.

## Research rules

- Search miss over incomplete capture means `UNKNOWN`, not absence.
- Raw session artifacts remain source evidence; SQLite FTS is a regeneratable view.
- Real conversation text, raw captures, private URLs, Drive IDs, conversation IDs, credentials, and account-specific metadata are not committed here.
- Public tests use synthetic fixtures only.
- Negative and inconclusive outcomes are first-class results.
- Green CI proves only the postconditions encoded by that workflow.

## Current research flow

`Issue -> experiment -> commit/PR -> execution -> evidence -> verification -> receipt -> disposition`

See [Architecture](docs/architecture.md), [Capture adapter contract](docs/capture-adapter-contract.md), [Mixed capture artifact recovery](docs/mixed-artifact-recovery.md), and [Research lifecycle](docs/research-lifecycle.md).

## Cumulative multi-session corpus

The normal workflow can accumulate many independent chats and repeated captures into one searchable corpus. Portable capture artifacts remain durable private evidence; accepted-artifact ledger membership is the durable corpus boundary; `corpus.sqlite3` and FTS are regeneratable projections.

Configure the private corpus location once. POSIX shell:

```bash
export SESSION_SEARCH_CORPUS=/private/path/session-search-corpus
```

PowerShell:

```powershell
$env:SESSION_SEARCH_CORPUS = 'C:\private\session-search-corpus'
```

Then ingest one or more verified portable captures without database surgery:

```bash
python3 -m session_search.corpus ingest capture-a.zip capture-b.zip
```

For a transport-neutral automated handoff, point the one-shot inbox command at a directory of portable ZIP artifacts:

```bash
python3 -m session_search.handoff --inbox /path/to/inbox --corpus /private/path/session-search-corpus --json
```

The handoff scans top-level ZIPs in deterministic filename order, delegates all corpus semantics to the existing idempotent ingest primitive, leaves source files untouched, and writes a private durable handoff receipt for every attempted file including `INGESTED`, `ALREADY_INGESTED`, and `FAILED`. A scheduler, Drive adapter, browser helper, or provider integration may invoke this seam; none becomes core authority.

Search all accepted sessions together:

```bash
python3 -m session_search.search "previous decision"
```

For continuity reconstruction where exact wording may differ, opt into broader lexical recall:

```bash
python3 -m session_search.search "lightweight IDE" --recall
```

`--recall` keeps strict search as the default. It broadens FTS candidate generation to token-OR matching, oversamples candidates, then reranks with session-level token coverage and session-diverse output so one large evidence dump cannot monopolize the result window. This is still lexical historical retrieval, not semantic memory, and a miss remains `UNKNOWN`.

For an explicit local controlled-vocabulary lookup, opt into reconciliation with a JSON registry:

```bash
python3 -m session_search.search "retrieval augmented generation" \
  --reconcile-registry /path/to/concepts.json --json
```

Registry schema v0 contains `id`, `preferred_label`, and `aliases` for each concept. Reconciliation uses exact normalized forms only. It expands aliases only after strict search misses and only when one controlled concept matches. Multiple matching concepts return `AMBIGUOUS_CONCEPT`; no semantic winner is chosen. Alias-expanded and recall-only hits are `CANDIDATE_ONLY`, while strict lexical hits are typed separately as `LEXICAL_EVIDENCE_ELIGIBLE`. Every hit remains `NON_AUTHORITATIVE_RETRIEVAL`: accepted artifacts/messages remain historical evidence authority. `--recall` is independent and must still be requested explicitly. No external vocabulary lookup, embedding model, or new persisted state is used.

Minimal registry example:

```json
{
  "schema": "theseus.session-search-controlled-concepts.v0",
  "concepts": [
    {
      "id": "retrieval-augmented-generation",
      "preferred_label": "retrieval augmented generation",
      "aliases": ["RAG"]
    }
  ]
}
```

Strict search combines query tokens with `AND` within one indexed message. That is useful for precise lookup, but a natural-language query can produce a lexical false negative when relevant facts are distributed across several messages, inflection changes a token, or an otherwise helpful extra term is absent from the matching message. A zero-hit strict query therefore does **not** establish a capture or corpus coverage gap.

For continuity reconstruction, use a bounded Sonar-style escalation before classifying history as absent:

1. try several short, independent discriminating anchors;
2. include at least one functional rephrase that does not merely repeat the expected wording;
3. if strict results are empty or weak, repeat the broader query with `--recall`;
4. compare sessions, provenance, coverage state, conflicts, and repeated fragments before deciding whether the result is a retrieval false negative, a real coverage gap, or still `UNKNOWN`.

Repeated retrieval of the same fragment is not independent evidence. This escalation is a retrieval discipline around Session Search, not a claim that the FTS projection is semantic memory, and it should remain bounded rather than turning every lookup into a full federated-memory traversal.

Each corpus search hit carries session identity, title, coverage state, message time, role, search class, and score so the assistant can distinguish relevance from evidence completeness. Restrict a query when needed:

```bash
python3 -m session_search.search "previous decision" --session <stable-session-id>
```

Verify durable membership, immutable artifact bytes, relational/FTS invariants, and coverage summaries:

```bash
python3 -m session_search.corpus verify
```

Inspect the observed corpus watermark without pretending the archive is current with any live provider:

```bash
python3 -m session_search.corpus status --json
```

`status` reports the latest accepted artifact time, latest observed message time, coverage-state counts, source-adapter counts, and projection verification state. Without an external authoritative source watermark it reports `currentness=UNKNOWN_WITHOUT_SOURCE_WATERMARK`. Session coverage, corpus freshness, and live-provider completeness are separate claims.

Rebuild the disposable SQLite/FTS projection only from accepted-ledger artifacts:

```bash
python3 -m session_search.corpus rebuild
```

Inspect an active writer lock without breaking it:

```bash
python3 -m session_search.corpus lock-status
```

An explicit `--corpus PATH` always overrides `SESSION_SEARCH_CORPUS`. There is no hidden default corpus directory. `ingest` and `rebuild` serialize through one corpus mutation lock; search remains read-only.

Real corpus directories contain raw private evidence and receipts and must never be committed to this public repository.

### Official DeepSeek export adapter

Official DeepSeek `conversations.json` exports can be transcoded into the same portable session-artifact contract used by the rest of Session Search. One provider export may contain many conversation graphs. A linear graph materializes one immutable child artifact; a branched graph materializes one root-to-leaf transcript variant per leaf so alternate continuations are never flattened into a fictitious sequential dialogue. Every child records the parent export SHA-256 and uses content-addressed naming, so later re-exports cannot overwrite earlier evidence.

Materialize portable artifacts only:

```bash
python3 -m session_search.deepseek_export conversations.json --output-dir ./deepseek-artifacts
```

Or ingest the official export directly into a cumulative corpus:

```bash
python3 -m session_search.deepseek_export conversations.json --corpus /private/path/session-search-corpus
python3 -m session_search.search "previous decision" --corpus /private/path/session-search-corpus
```

The adapter treats the DeepSeek `parent`/`children` graph as ordering authority, maps `REQUEST` to user dialogue and `RESPONSE` to assistant dialogue, preserves explicit empty fragment collections as trace placeholders, preserves missing timestamps as unknown rather than inventing epoch values, and blocks missing/malformed fragment collections or inconsistent graph shapes instead of guessing. Fragment identities use an unambiguous tuple encoding, and direct corpus ingest reuses the same source snapshot hash that produced the child artifacts. Public tests use synthetic exports only; real export bytes, conversation text, and account-specific identifiers remain private.

### Official ChatGPT account export adapter

Official ChatGPT account-export ZIPs can be transcoded into portable Session Search artifacts without flattening provider branches. The adapter accepts either `conversations.json` or numbered `conversations-NNN.json` shards, reconstructs child edges from each node's authoritative `parent` link, validates a single rooted acyclic graph, and materializes one root-to-leaf artifact for every observable terminal branch.

```bash
python3 -m session_search.chatgpt_export chatgpt-export.zip --output-dir ./chatgpt-artifacts
```

Or ingest directly into the cumulative corpus:

```bash
python3 -m session_search.chatgpt_export chatgpt-export.zip --corpus /private/path/session-search-corpus
```

Direct corpus ingest performs branch reconciliation against already accepted legacy/raw captures before publishing the derived SQLite projection. Exact `(message_id, canonical payload)` overlap is branch evidence; raw provider-only messages absent from every official branch do not vote. Branch-discriminating evidence routes a legacy artifact to one explicit branch, conflicting evidence fails closed, and common-only or zero-overlap evidence is isolated under a deterministic `~unresolved-<hash>` projection session rather than silently becoming the base branch. Multimodal text projections can use their cryptographically bound raw-content lineage as exact branch evidence. Accepted artifact bytes and accepted-ledger `session_id` values remain immutable source evidence; routing lives only in the versioned derived projection.

Branch identity is independent of `current_node`: at each fork the earliest uniquely timestamped child defines the stable base branch, while other choices receive a deterministic `~branch-<hash>` suffix. `current_node` is validated as an observed terminal node and recorded as provenance only. Ambiguous sibling ordering, broken parents, cycles, disconnected graphs, duplicate identities, incomplete branch families, and malformed mappings fail closed. Account exports are conservatively reported as `PARTIAL_SESSION_SLICE` because a downloaded snapshot is not proof of complete authoritative platform history.

The branch-aware projection schema is `session-search-corpus-v2`. An existing v1 corpus is durable evidence, not a compatible writable projection: `corpus verify` reports reconciliation required and `corpus rebuild` regenerates v2 atomically from the unchanged accepted ledger and artifact blobs. Do not edit ledger entries or SQLite rows manually to repair branch identity.

Plain user/assistant text is indexed as dialogue. `thoughts` and `reasoning_recap` remain hidden evidence under the existing normalization contract. For `multimodal_text`, textual parts and audio transcriptions are projected into searchable dialogue while the full provider multimodal payload and message metadata are retained in a non-dialogue trace record. Child artifacts bind the parent ZIP SHA-256 and use immutable content-addressed names. Public regression tests use synthetic fixtures only; private export bytes, text, IDs, URLs, and source digests are never committed.

### Official xAI/Grok export adapter

Official xAI data-export ZIPs can be transcoded into the same portable Session Search artifact contract without a provider-specific database or search path. The adapter reads the single `prod-grok-backend.json` payload from the official ZIP, binds every child artifact to the parent ZIP SHA-256, and uses content-addressed immutable child names.

Materialize portable artifacts only:

```bash
python3 -m session_search.xai_export xai-export.zip --output-dir ./xai-artifacts
```

Or ingest directly into an existing cumulative corpus:

```bash
python3 -m session_search.xai_export xai-export.zip --corpus /private/path/session-search-corpus
python3 -m session_search.search "previous decision" --corpus /private/path/session-search-corpus
```

For xAI, `parent_response_id` is graph authority because official exports may omit or partially populate `children`. When `leaf_response_id` is present, that root-to-leaf path is indexed as normal dialogue and off-path alternatives are retained as `trace`. Without explicit active-leaf metadata, a unique leaf is unambiguous; multiple leaves materialize explicit branch variants rather than guessing. `human` maps to user dialogue, `assistant`/`ASSISTANT` map to assistant dialogue, and unrecognized sender values remain trace. Mongo-style millisecond timestamps are parsed deterministically; missing time remains unknown.


### Speed Booster Toolkit ChatGPT export adapter

Speed Booster Toolkit JSON exports can be transcoded into the same portable Session Search artifact contract without making the browser extension a runtime dependency. The observed export shape is one sequential chat slice with top-level `title`, `exported_at`, `created_at`, and `messages[]` records containing role, timestamp, model, text, and optional source/image metadata.

Materialize one portable artifact:

```bash
python3 -m session_search.speed_booster_export chat-export.json --output-dir ./speed-booster-artifacts
```

If that artifact will be ingested into an existing corpus that may already contain Speed Booster artifacts created by an older adapter revision, pass the corpus as identity context while materializing:

```bash
python3 -m session_search.speed_booster_export chat-export.json --output-dir ./speed-booster-artifacts --existing-corpus /private/path/session-search-corpus
```

Without that context, generic corpus ingest fails closed with `BLOCKED_SPEED_BOOSTER_LEGACY_IDENTITY_CONTEXT_REQUIRED` when accepted legacy evidence proves the artifact would otherwise create a duplicate semantic session. Re-materialize the source with `--existing-corpus` rather than editing the artifact or corpus state manually.

Or ingest the export directly into a cumulative corpus:

```bash
python3 -m session_search.speed_booster_export chat-export.json --corpus /private/path/session-search-corpus
python3 -m session_search.search "previous decision" --corpus /private/path/session-search-corpus
```

Because this format does not expose the authoritative ChatGPT conversation graph or pagination boundary, every imported export is conservatively marked `PARTIAL_SESSION_SLICE`. Adapter v1 derives a versioned synthetic session identity from `first message timestamp + first-message role/content`; `exported_at` is provenance only, so a later export with an appended tail resolves to the same semantic session and stable per-message IDs deduplicate the unchanged prefix. Exports that share title and first timestamp but differ in their opening message no longer collapse into one synthetic session. Direct corpus ingest reads the source once and reuses that same snapshot hash for the child manifest and returned provenance receipt. User/assistant roles remain dialogue, unsupported roles remain trace with their raw role preserved, and export/message metadata is retained as provenance rather than search authority.

Public tests use synthetic fixtures only. Real extension exports, account-specific data, source file identifiers, and conversation text remain private.

### Legacy scratch projection

The original one-artifact path remains available for debugging, portable reproduction, and scratch projections:

```bash
python3 -m session_search.importer capture.zip --db session-search.sqlite3
python3 -m session_search.search "previous decision" --db session-search.sqlite3
```

`--db` treats that SQLite file as a legacy/scratch projection. It does not opt the file into cumulative corpus authority.

## Portable prototype

Import a compatible artifact:

```bash
python3 -m session_search.importer capture.zip --db session-search.sqlite3
```

Search visible dialogue and tool evidence:

```bash
python3 -m session_search.search "previous decision" --db session-search.sqlite3
```

Opt into assistant/tool trace when debugging importer behavior:

```bash
python3 -m session_search.search "workspace_shell" --db session-search.sqlite3 --scope trace
```

Default search excludes hidden/system/thought-like content.

### Multi-page capture

The importer accepts a conversation-detail payload plus any captured paginated `conversation-messages` payloads from the same artifact. It preserves each payload page as provenance, merges messages deterministically, deduplicates identical repeated message IDs, rejects conflicting duplicate message IDs, and computes coverage from the oldest/newest observed page boundaries.

```text
PARTIAL_SESSION_SLICE
  oldest observed page still has_previous_page=true

COMPLETE_EXPOSED_CONVERSATION
  oldest observed page has_previous_page=false
```

A sanitized real-world validation observed 13 captured payload pages and 2,034 unique message IDs with zero duplicate occurrences, reaching `COMPLETE_EXPOSED_CONVERSATION`. Raw session content and source identifiers remain private.

## Development roadmap

1. Manual browser capture -> manual transport -> local import/search.
2. Portable importer/index/search without MarcoPolo.
3. Automated capture transport and index refresh.
4. Direct session integration without manual ZIP handoff.
5. Browserless authoritative session source.

## Wiki bootstrap

```text
Wiki: WIKI_GIT_REMOTE_VERIFIED
```

The one-time manual bootstrap is complete. The first `Home` page created the Wiki Git remote; the research navigation was then seeded through the governed Wiki wrapper.

Verified Wiki seed:

```text
branch: master
commit: fab484e1e22c982229d2aab2d80c933f4c5c1d93
```

The Wiki now contains `Home`, `Terminology`, `Architecture`, `Capture-Adapters`, `Session-Artifact-Contract`, `Session-Search-Contract`, `Experiment-Traceability`, and `Research-Lifecycle`. Wiki remains a human navigation layer; canonical contracts, experiments, receipts, tests, and source history remain in the main repository.

## Verification

Canonical development QA:

```bash
./tools/dev/check
```

The gate composes the existing repository verifier, full unit/regression suite, Python syntax check, and patch hygiene. Live/private corpus verification remains explicit:

```bash
python3 -m session_search.corpus verify --corpus <corpus-root>
python3 -m session_search.corpus rebuild --corpus <corpus-root>
```

See [QA and verification](docs/qa.md) for the claim boundary of each check.
