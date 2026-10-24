# Session Search Arcade materializer

This package is the Arcade Hosted consumer for `theseus.session-search-shard-bundle.v1`.

Current surface: **read-only inspection only**.

`inspect_bundle(drive_file_id, expected_bundle_sha256)`:

1. obtains a Google OAuth token from Arcade using `drive.readonly`;
2. downloads exactly one private Drive file by ID;
3. checks the externally supplied bundle SHA-256 before acceptance;
4. independently validates ZIP members, CSV shape, file digests, source projection digest, source manifest digest, counts, and generation identity;
5. returns aggregate evidence only.

It does not contain Neon credentials, open Postgres connections, create/mark READY generations, activate generations, or mutate a serving catalog.

## Local validation

From this directory:

```bash
python -m pip install -e .
python -c 'from session_search_arcade_materializer.server import app; print(type(app).__name__)'
```

Arcade CLI deployment requires an authenticated Arcade context:

```bash
arcade login
arcade deploy -e src/session_search_arcade_materializer/server.py --secrets skip
```

For non-interactive CI, Arcade supports `ARCADE_URL` plus a project `ARCADE_API_KEY`. The repository workflow `.github/workflows/deploy-session-search-arcade-materializer.yml` is manual-only and uses that route.

No deployment or external mutation happens merely by merging this package/workflow.
