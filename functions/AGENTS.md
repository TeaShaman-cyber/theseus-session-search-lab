# Code Review Rules

## Telemetry coverage and production wiring

When a change adds or modifies telemetry, semantic events, or result reporting for
a user-visible outcome, review every externally reachable path that can produce
that outcome and the production entrypoint wiring.

A handler test that injects telemetry directly, or coverage of only one route,
does not prove that production traffic reaches the telemetry path. In particular,
check alternate protocol/transport paths such as HTTP and MCP when both can
produce the same outcome.

Safe paths are either:

- shared emission/reporting logic used by every relevant route; or
- explicit per-route regression coverage plus a production-wiring test/readback.

Do not infer live telemetry coverage from code or tests alone. A claim about
deployed/live coverage requires current runtime evidence.
