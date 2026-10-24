# Arcade bundle consumer

## Purpose

The Arcade Hosted consumer removes MarcoPolo from the steady-state Session Search corpus byte path.

```text
private Google Drive bundle
        -> Arcade Google OAuth
        -> independent bundle verification
        -> [future] Neon BUILDING loader
```

The first accepted slice is deliberately read-only. It proves Drive acquisition and bundle verification before granting the Hosted server any Neon secret or mutation capability.

## Tool contract

`inspect_bundle(drive_file_id, expected_bundle_sha256)` requires the Arcade Google OAuth provider with scope:

`https://www.googleapis.com/auth/drive.readonly`

The expected bundle SHA-256 is mandatory and must come from accepted source evidence outside the Drive file itself. Internal self-consistency alone is not authority.

The consumer streams the Drive file to ephemeral storage with a 512 MiB cap, hashes bytes during download, rejects a SHA mismatch, then verifies the full `session-search-shard-bundle.v1` provenance chain independently of the producer implementation.

Returned data is aggregate evidence only. Private message/session content and OAuth tokens are never returned.

## Current acceptance

A clean virtual environment installed the package using current Arcade components observed on 2026-10-24:

- `arcade-mcp 1.16.3`;
- `arcade-mcp-server 1.32.1`;
- `arcade-core 4.23.0`.

The Hosted server registered the single `inspect_bundle` tool successfully. A compatibility issue with postponed annotations in the entrypoint was reproduced and removed before acceptance.

The independent consumer verified the real xAI Drive bundle:

- bundle SHA-256 `7ccf184964a3243f99c7d7baebf44f3906886ca70d3a450842291e0e63a9ae63`;
- 60 sessions;
- 17,790 searchable messages;
- generation `6095e643b3fc4f133c94c04db24565dce6bbd456a308860820e94e70f8047835`.

The Arcade project has an active Google OAuth provider. Actual Hosted deployment is currently **BLOCKED_NO_CLI_AUTH** in the MarcoPolo workspace: `arcade whoami` reports no logged-in CLI context and no non-interactive `ARCADE_API_KEY` is present there.

## Deployment IaC

`.github/workflows/deploy-session-search-arcade-materializer.yml` provides a manual-only non-interactive deployment route using repository secrets:

- `ARCADE_URL`;
- `ARCADE_API_KEY` (Arcade project API key).

The workflow does not run automatically. Adding those secrets and explicitly dispatching it crosses the external deployment boundary.

## Next boundary

Only after Hosted `inspect_bundle` is deployed and exercised against the exact private xAI Drive file should the server gain a Neon control/data-plane secret and a `materialize_bundle` operation.

That mutation-capable tool must remain BUILDING-only and separate from READY/activation/catalog promotion.
