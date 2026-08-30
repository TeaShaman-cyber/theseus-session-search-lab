# Theseus Session Search Lab

Theseus Session Search Lab is a public research line under the [Theseus public-interest research program](https://github.com/TeaShaman-cyber/theseus-research). This repository does not define the Theseus program contract.

**Session history is evidence, not semantic memory. Search indexes are derived projections, not authority.**

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

## Development roadmap

1. Manual browser capture -> manual transport -> local import/search.
2. Portable importer/index/search without MarcoPolo.
3. Automated capture transport and index refresh.
4. Direct session integration without manual ZIP handoff.
5. Browserless authoritative session source.

## Wiki bootstrap

```text
Wiki: ENABLED / MANUAL_FIRST_PAGE_REQUIRED
```

GitHub does not create the `.wiki.git` remote merely because Wiki is enabled. A human must open the Wiki tab and **Create the first page** once. After that one-time bootstrap, Wiki maintenance is normal governed Git work with remote SHA readback.

## Verification

```bash
python3 scripts/verify_repo.py
python3 -m unittest discover -s tests -v
```
