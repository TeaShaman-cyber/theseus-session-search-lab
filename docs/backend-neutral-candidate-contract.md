# Backend-neutral candidate contract v1

Coordination: issue #128. This contract is a research/conformance seam; it is not a
serving cutover or reranker selection.

Pipeline boundary:

`Candidate[] -> Ranker -> RankedCandidate[] -> ExactGroup/Select -> GroupedResult[]`

Exact grouping from issue #188 is intentionally downstream of ranking. Candidate v1
exists so local SQLite and serverless shard results can enter the same evaluation
harness without pretending backend-specific ranking numbers have identical semantics.

## Candidate v1

Schema identifier: `theseus.session-search-candidate.v1`.

Required canonical fields:
- `session_id`: non-empty string;
- `ordinal`: non-negative integer;
- `search_class`: `dialogue`, `evidence`, or `trace`;
- `text`: exact candidate body;
- `backend_score`: finite number copied from the backend result;
- `backend_score_order`: `lower_better` or `higher_better`.

Optional provenance/context fields are retained when available:
`session_title`, `session_coverage`, `message_id`, `role`, `content_type`,
`create_time`, `provider`, `shard_id`, `generation_id`, `shard_rank`.

Unknown backend-private fields are not projected into v1.

## Score boundary

`backend_score` is **not a normalized cross-backend score**. Its only portable meaning
comes with `backend_score_order`.

- SQLite FTS5/BM25 uses `lower_better` in the current local path.
- Postgres `ts_rank_cd` uses `higher_better` in the current serverless path.

A backend-neutral reranker may use the raw score only when its algorithm explicitly
handles that backend's semantics. It must not sort a mixed candidate set by raw
`backend_score` alone. Cross-backend fusion requires an independently justified
normalization/rank-fusion rule.

## Identity/provenance boundary

`message_id` is not globally unique and must never be the sole candidate identity.
At minimum preserve projection/session context plus ordinal/message provenance; serverless
paths additionally preserve provider/shard/generation when available.

Candidate v1 is retrieval evidence, not source authority. Accepted artifacts/ledger remain
historical authority.

## Conformance

Python projector: `session_search.candidate_contract.project_candidate_v1`.
JavaScript projector: `functions/candidate-contract.mjs`.
Shared fixture: `tests/fixtures/candidate_v1.json`.

Both implementations must produce exactly the same canonical object for the shared local
and serverless-shaped inputs. Ambiguous score orientation fails closed.

## Non-goals

- no reranker choice;
- no score normalization algorithm;
- no embeddings/vector layer;
- no backend migration;
- no production API change.

## Known-path adapters

To avoid repeating score-orientation choices at call sites:
- Python/local: `project_local_candidate_v1(row)` binds `lower_better`;
- JavaScript/serverless: `projectServerlessCandidateV1(row)` binds `higher_better`.

The generic projectors remain available for controlled experiments with other backends,
but callers must supply the orientation explicitly and invalid values fail closed.
