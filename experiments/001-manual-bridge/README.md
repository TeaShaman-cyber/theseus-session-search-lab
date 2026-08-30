# Experiment 001 — manual capture/transport development bridge

## Question

Can a manually captured browser-session artifact be integrity-checked, normalized, indexed, and searched well enough to justify a portable Session Search research line?

## Sanitized observed result

A private local development corpus verified:

- source schema: `barn-doctor-export:v1`;
- Barn Doctor version: `0.2.3`;
- optional conversation payload: direct UTF-8 JSON;
- observed normalized messages: 323;
- coverage: partial session slice because an earlier page existed;
- projection: SQLite FTS5;
- local retrieval: verified;
- default search excludes system/thought/reasoning-recap/model-editable-context content.

## Boundary

The real ZIP, conversation text, private source identifiers, Drive identifiers, conversation identifiers, and private metadata are not part of this public repository.

## Disposition

`VERIFIED_DEVELOPMENT_PROTOTYPE / NOT_RUNTIME_ARCHITECTURE`

MarcoPolo was used as a development workbench. It is not required by the target runtime.
