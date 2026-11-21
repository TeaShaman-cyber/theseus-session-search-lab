# Verification: Router Failure Isolation

## Requirements

### Requirement: Bounded shard failure isolation

#### Scenario: One stalled shard cannot block healthy results indefinitely

- **GIVEN** a federated search selects at least two READY shards
- **AND** one shard returns a healthy result while another shard never resolves its request
- **WHEN** the router reaches the configured application deadline for the stalled shard
- **THEN** the stalled shard is classified as unavailable for that request
- **AND** the router completes without waiting indefinitely
- **AND** the response is PARTIAL
- **AND** the healthy shard result and its provenance are preserved
