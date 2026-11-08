# Retrieval experience and explicit feedback

Coordination: issue #215. Parent causal research: #188. Operational observability: #109.

## Plain mechanism

Session Search should not begin every retrieval episode with zero operational
experience. The assistant/user may explicitly report what was wrong with one
retrieval, what bounded operator was tried next, and whether the observed outcome
improved, regressed, stayed the same, was mixed, or remained unknown.

This is not model fine-tuning and is not online policy mutation.

Flow:

    search
      -> explicit assessment
      -> feedback episode
      -> periodic feedback refresh
      -> pattern projection
      -> later policy candidate
      -> independent validation

An episode is evidence. A pattern is an aggregate. A policy candidate is a
separate validation object. They are not interchangeable.

## Future service surface

A serving version that accepts feedback should expose an explicit operation with
the semantic shape:

    session_feedback(
        baseline_request_ref,
        result_request_ref?,
        failure_class,
        operator{name, version},
        outcome,
        notes?
    )

This deliberately resembles the interaction role of Acumen's explicit feedback
action: the intelligent consumer reports a concrete useful, off-target, missing
or harmful observation after search. No claim is made about Acumen's internal
storage or learning implementation.

### Search-side prerequisite

A search response should expose an opaque request_ref suitable for correlation.
The ref links privately to operational provenance such as trace, service version,
shards and generation without requiring raw query text to be copied into feedback,
Grafana labels, or public Git receipts.

The first implementation slice does not add this live write surface yet. It
versions and tests the data contract before creating a stateful serving endpoint.

## Feedback request v0

Schema: theseus.session-search-feedback-request.v0

Carries:
- baseline request ref;
- optional result request ref;
- failure class;
- retrieval operator name and version;
- observed outcome;
- optional bounded notes.

Raw query_text and target_text fields are forbidden by the v0 validator. A private
implementation may retain separately-authorized content under its own privacy and
storage contract; that content is not part of the portable public feedback
contract.

## Retrieval-experience episode v0

Schema: theseus.session-search-retrieval-experience.v0

Records:
- stable episode ID and observation time;
- failure class;
- operator/version;
- baseline/result references and bounded statuses or metrics;
- outcome: improved, same, regressed, mixed or unknown;
- confidence: FACT, INFERENCE, HYPOTHESIS or UNKNOWN;
- provenance references;
- aggregation_eligible.

Target-dependent oracle diagnostics and episodes with unmeasured outcomes may be
stored as evidence while being excluded from policy-oriented aggregation.

## Two independent maintenance streams

Conversation history and retrieval experience are separate inputs:

    accepted conversation artifacts
      -> corpus refresh
      -> search projection

    explicit retrieval feedback episodes
      -> feedback refresh
      -> experience/pattern projection

A corpus refresh must not erase feedback history. A feedback refresh must not
rewrite accepted conversation authority.

## Feedback refresh cycle

The deterministic v0 processor is:

    python3 scripts/process_retrieval_feedback.py episodes.jsonl patterns.json

It validates every episode and groups only comparable failure_class + operator +
operator_version observations. It keeps:
- total observed episodes;
- aggregation-eligible vs ineligible counts;
- improved, same, regressed, mixed and unknown separately.

It never emits an automatically promoted policy. policy_candidates is empty and
policy_promotion is NOT_PERFORMED in v0.

This processor is designed to become a regular maintenance job once the live
feedback-write path exists. Scheduling and cadence are intentionally not
hard-coded in v0; the job can later run on a bounded time or episode-count trigger
without changing the evidence contract.

## Seed evidence

The initial public sanitized seed set is hand-audited rather than mined blindly:
- #182: one term-preservation improvement and one regression;
- #203: cap 3 to 7 produced no gain;
- accepted history: a noisy Session Search episode followed by a fallback route,
  but without a measured target improvement, therefore unknown and ineligible;
- PR #214 diagnostic: lemma-oracle headroom, explicitly target-dependent and
  therefore ineligible for policy aggregation.

The public seed records contain no raw query or target text.

## Strict JSON / JSONL QA

Feedback evidence is fail-closed at the serialization boundary.

The shared strict parser rejects:
- duplicate object keys at any nesting level;
- non-standard JSON constants such as NaN and Infinity;
- trailing JSON content;
- malformed UTF-8 / malformed JSON.

The feedback JSONL loader additionally rejects:
- blank records;
- duplicate episode_id values within one ledger.

The v0 feedback/episode contracts also fail closed on:
- unknown top-level fields;
- unknown operator/provenance fields;
- raw query/target keys at any nested object depth.

This prevents typo fields from being silently ignored and prevents private text
from being hidden inside nested metadata while still passing the public contract.

Canonical repository QA runs scripts/verify_strict_json.py over every tracked
.json and .jsonl file, so the same duplicate-key/non-standard-constant invariant
also covers receipts, fixtures, package metadata and Grafana JSON checked into Git.

Stable older import paths that already have their own strict duplicate-key parsing
remain unchanged by this slice; this gate adds a common repository-wide backstop
rather than refactoring unrelated ingestion behavior.

## Relationship to Grafana

Grafana #109 remains the operational observability plane. Current traces provide
status/error class, latency, shard/provider/generation and deployed service
version. They do not currently carry raw query text, search-mode semantics, or
retrieval-operator feedback.

That separation is intentional. A future episode may privately correlate to a
Grafana trace/request ref, while Grafana itself remains a sanitized operational
surface.

## Promotion boundary

Regular feedback processing may create evidence-backed patterns, but changing
serving behavior remains a separate step:

    pattern
      -> frozen policy candidate
      -> new independent holdout
      -> QA, regression and cost checks
      -> explicit project promotion path

No episode count or majority vote bypasses that boundary.
