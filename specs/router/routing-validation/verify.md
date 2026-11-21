# Verification: Routing Validation

## Requirements

### Requirement: Validate routing filters before shard selection

#### Scenario: Invalid session identifier fails before routing

- **GIVEN** a search request with a session_id that violates the public input contract
- **WHEN** the router validates the request
- **THEN** it rejects the request before shard selection
- **AND** it does not return NO_SIGNAL or PARTIAL

#### Scenario: Non-finite time bound fails before routing

- **GIVEN** a search request whose from_time or to_time is not finite
- **WHEN** the router validates the request
- **THEN** it rejects the request before shard selection
- **AND** no shard is selected using that invalid bound

#### Scenario: Inverted time bounds fail before routing

- **GIVEN** a search request with from_time greater than to_time
- **WHEN** the router validates the request
- **THEN** it rejects the request before shard selection
- **AND** it does not report a searched miss or PARTIAL result
