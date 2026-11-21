# Routing Validation

## Purpose

Session Search must reject malformed routing filters before those values influence shard selection. Invalid questions must fail explicitly rather than being translated into misleading NO_SIGNAL or PARTIAL outcomes.

## Requirements

### Requirement: Validate routing filters before shard selection

The router MUST validate session and time filters before using them to select shards.

A session_id that does not satisfy the public input contract MUST fail explicitly before shard selection.

from_time and to_time MUST be finite numeric values when provided. If both are provided and from_time is greater than to_time, the request MUST fail explicitly before shard selection.

Malformed or contradictory routing filters MUST NOT be interpreted as legitimate historical absence, shard unavailability, NO_SIGNAL, or PARTIAL.

## Constraints

- Validation MUST occur before selectShards observes the filter values.
- Validation failure MUST be distinguishable from a searched miss.
- Routing validation MUST not depend on shard contents or catalog coincidence.

## Spec Dependencies

_none_
