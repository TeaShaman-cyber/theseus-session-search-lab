# Portable Session Search runtime

The portable runtime is a deterministic ZIP containing the tracked `session_search` Python package, this note, and a versioned runtime manifest. It carries **no corpus data, credentials, MarcoPolo dependency, Google Drive dependency, or third-party Python package**.

## Runtime contract

- Python 3.11 or newer.
- Python standard library only.
- SQLite must include FTS5 support (the hosted Ubuntu/Python acceptance environment does).
- The ZIP manifest records the exact source repository/revision and SHA-256/size of every payload member.
- The producer sidecar receipt records the exact ZIP SHA-256 and manifest SHA-256.
- A packaged archive proves only what was built from the stated source revision. It does not prove freshness or completeness of any later corpus.

## Local use

Unpack the ZIP into an empty directory and run modules from that directory:

```bash
unzip session-search-runtime.zip -d session-search-runtime
cd session-search-runtime

python3 -m session_search.deepseek_export conversations.json --corpus ./corpus
python3 -m session_search.corpus verify --corpus ./corpus --json
python3 -m session_search.corpus status --corpus ./corpus --json
python3 -m session_search.search "previous decision" --corpus ./corpus --json
```

The runtime also contains the xAI/Grok, Speed Booster, Barn Doctor portable-artifact ingest, Barn provenance-recovery, and one-shot source-agnostic inbox handoff entrypoints already present in the source revision. Transport/acquisition remains outside the core runtime contract.

## Authority boundary

`runtime-manifest.json` and the producer receipt establish package provenance/integrity for one built artifact. Corpus authority remains the accepted-artifact ledger plus immutable accepted artifact bytes. `corpus status` reports observed watermarks and remains `UNKNOWN_WITHOUT_SOURCE_WATERMARK` unless a separate acquisition layer supplies an authoritative source watermark.
