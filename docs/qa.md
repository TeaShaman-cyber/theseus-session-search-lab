# QA and verification

Session Search has one canonical repository-development gate:

```bash
./tools/dev/check
```

The gate composes existing checks rather than defining a second verification system.

## Verification layers

| Layer | Existing mechanism | Claim established |
| --- | --- | --- |
| Public repository contract / privacy | `python3 scripts/verify_repo.py` | Required public files and sanitized receipts exist; forbidden private/binary corpus artifacts and obvious secret patterns are not tracked. |
| Unit and regression behavior | `python3 -m unittest discover -s tests -p 'test_*.py'` | Artifact normalization, corpus ingest, provider adapters, retrieval, reconciliation, rebuild, and known regressions behave as encoded by tests. |
| Python parse/import sanity | `python3 -m compileall -q session_search scripts tests` | Tracked Python sources compile syntactically. |
| Patch hygiene | `git diff --check` | Current tracked/staged patch has no whitespace errors. |
| Live corpus authority/integrity | `python3 -m session_search.corpus verify --corpus <path>` | Accepted ledger and immutable artifacts are valid and the current SQLite/FTS projection derives from accepted artifact evidence. |
| Corpus evidence watermark | `python3 -m session_search.corpus status --corpus <path>` | Reports observed accepted/message watermarks and coverage/source counts; does **not** prove live-provider freshness. |
| Projection repair / equivalence | `python3 -m session_search.corpus rebuild --corpus <path>` | A fresh disposable projection can be regenerated from accepted artifacts and atomically replace the old projection only after verification. |

The live corpus commands operate on private evidence and therefore are not part of public CI. Public CI exercises the same contracts with synthetic fixtures.

## Derived-state rule

`corpus.sqlite3` and FTS are disposable projections, never authority. A projection is not `VERIFIED` merely because SQLite integrity, row counts, and ledger metadata agree internally. Verification must bind the searchable semantic/provenance state back to the accepted ledger plus immutable artifact bytes.

Branch routing is derived state under the same rule. Projection schema `session-search-corpus-v2` stores one versioned artifact route per accepted artifact, preserving the accepted ledger `session_id` separately from the projected branch session. `verify` checks route membership/version and rebuild derivability; an old v1 projection reports reconciliation required until `rebuild` regenerates v2 from immutable accepted evidence.

This rule is regression-covered in `tests/test_corpus_store.py` and tracked by Issues #28 and #43.

## Adding QA

Prefer extending an existing layer before adding another checker:

1. product behavior or a reproduced bug -> add a focused test under `tests/`;
2. public repository/privacy invariant -> extend `scripts/verify_repo.py`;
3. generic mechanical pre-review check -> add it to `tools/dev/check`;
4. private/live-corpus acceptance -> use `session_search.corpus verify` / `rebuild` and record only sanitized receipts or conclusions publicly.

An unrun or uncovered check is `UNKNOWN`, never PASS. Search results, SQLite state, CI status, and receipt existence do not independently grant authority beyond the claim each layer verifies.

## Currentness boundary

Session coverage, corpus freshness, and live-provider completeness are different claims. A `COMPLETE_EXPOSED_CONVERSATION` session can still live inside a corpus whose newest accepted capture is old. Conversely, a recently accepted partial capture does not prove complete history.

Without an authoritative acquisition/source watermark, `corpus status` must report `UNKNOWN_WITHOUT_SOURCE_WATERMARK`; wall-clock age alone is not enough to label an archive `CURRENT` or `STALE`.
