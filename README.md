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

## Research rules

- Search miss over incomplete capture means `UNKNOWN`, not absence.
- Raw session artifacts remain source evidence; SQLite FTS is a regeneratable view.
- Real conversation text, raw captures, private URLs, Drive IDs, conversation IDs, credentials, and account-specific metadata are not committed here.
- Public tests use synthetic fixtures only.
- Negative and inconclusive outcomes are first-class results.
- Green CI proves only the postconditions encoded by that workflow.

## Current research flow

`Issue -> experiment -> commit/PR -> execution -> evidence -> verification -> receipt -> disposition`

See [Architecture](docs/architecture.md), [Capture adapter contract](docs/capture-adapter-contract.md), and [Research lifecycle](docs/research-lifecycle.md).

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

Search all accepted sessions together:

```bash
python3 -m session_search.search "previous decision"
```

Each corpus search hit carries session identity, title, coverage state, message time, role, search class, and score so the assistant can distinguish relevance from evidence completeness. Restrict a query when needed:

```bash
python3 -m session_search.search "previous decision" --session <stable-session-id>
```

Verify durable membership, immutable artifact bytes, relational/FTS invariants, and coverage summaries:

```bash
python3 -m session_search.corpus verify
```

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

```bash
python3 scripts/verify_repo.py
python3 -m unittest discover -s tests -v
```
