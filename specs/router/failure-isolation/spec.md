# Router Failure Isolation

## Purpose

Federated Session Search must continue returning useful healthy-shard results when one selected shard becomes unavailable or stalls. This spec captures the existing failure-isolation invariant that prevents one shard from turning a bounded partial response into an indefinitely blocked request.

## Requirements

### Requirement: Bounded shard failure isolation

The router MUST bound each selected shard request independently. A shard that does not complete within the configured application deadline MUST be classified as unavailable for that request, while completed healthy-shard results remain eligible for the merged response.

When at least one selected shard is unavailable and at least one healthy shard completed, the router MUST return an explicit PARTIAL result rather than waiting indefinitely or discarding the healthy results.

## Constraints

- A transport connection that remains open without completing MUST NOT bypass the application deadline.
- Failure isolation MUST apply before the aggregate wait can block on every selected shard.
- Timeout handling MUST preserve the same provenance and deterministic merge semantics used for other unavailable-shard outcomes.

## Spec Dependencies

_none_
