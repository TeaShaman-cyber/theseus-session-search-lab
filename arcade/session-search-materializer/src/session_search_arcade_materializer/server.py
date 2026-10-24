import asyncio
import tempfile
from pathlib import Path
from typing import Annotated, Any

from arcade_mcp_server import Context, MCPApp
from arcade_mcp_server.auth import Google

from session_search_arcade_materializer.bundle_consumer import (
    download_drive_bundle,
    read_verified_bundle,
    verify_bundle,
)
from session_search_arcade_materializer.neon_loader import (
    LOADER_ROLE,
    load_rows_to_building,
    resolve_neon_connection_uri,
)

DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
app = MCPApp(name="session_search_materializer", version="0.2.0", log_level="INFO")


@app.tool(requires_auth=Google(scopes=[DRIVE_READONLY]))
async def inspect_bundle(
    context: Context,
    drive_file_id: Annotated[str, "Exact private Google Drive file ID for the shard bundle."],
    expected_bundle_sha256: Annotated[str, "Expected SHA-256 recorded from accepted source authority."],
) -> Annotated[dict[str, Any], "Aggregate verified shard-bundle evidence without private message content."]:
    """Download one exact Drive bundle and verify it without mutating Neon or any catalog."""
    token = context.get_auth_token_or_empty()
    if not token:
        raise ValueError("Google authorization is required")
    with tempfile.TemporaryDirectory(prefix="session-search-arcade-inspect-") as td:
        path = Path(td) / "bundle.zip"
        await download_drive_bundle(
            drive_file_id=drive_file_id,
            oauth_token=token,
            expected_bundle_sha256=expected_bundle_sha256,
            destination=path,
        )
        return verify_bundle(path, expected_bundle_sha256=expected_bundle_sha256)


@app.tool(
    requires_auth=Google(scopes=[DRIVE_READONLY]),
    requires_secrets=["NEON_API_KEY"],
)
async def materialize_bundle(
    context: Context,
    drive_file_id: Annotated[str, "Exact private Google Drive file ID for the shard bundle."],
    expected_bundle_sha256: Annotated[str, "Expected SHA-256 recorded from accepted source authority."],
    neon_project_id: Annotated[str, "Project-scoped Neon target project ID."],
    neon_branch_id: Annotated[str, "Exact Neon target branch ID."],
    neon_database_name: Annotated[str, "Neon database name."] = "neondb",
) -> Annotated[dict[str, Any], "BUILDING-only materialization result."]:
    """Verify one Drive bundle and prepare a least-privilege Neon BUILDING target connection."""
    token = context.get_auth_token_or_empty()
    if not token:
        raise ValueError("Google authorization is required")
    neon_api_key = context.get_secret("NEON_API_KEY")
    if not neon_api_key:
        raise ValueError("NEON_API_KEY is required")
    with tempfile.TemporaryDirectory(prefix="session-search-arcade-materialize-") as td:
        path = Path(td) / "bundle.zip"
        await download_drive_bundle(
            drive_file_id=drive_file_id, oauth_token=token,
            expected_bundle_sha256=expected_bundle_sha256, destination=path,
        )
        verified, sessions, messages = read_verified_bundle(
            path, expected_bundle_sha256=expected_bundle_sha256
        )
        database_url = await resolve_neon_connection_uri(
            neon_api_key=neon_api_key, project_id=neon_project_id,
            branch_id=neon_branch_id, database_name=neon_database_name,
        )
        loaded = await asyncio.to_thread(
            load_rows_to_building,
            manifest=verified, sessions=sessions, messages=messages,
            database_url=database_url,
        )
        return {
            **verified, **loaded,
            "neon_project_id": neon_project_id,
            "neon_branch_id": neon_branch_id,
            "neon_database_name": neon_database_name,
            "loader_role": LOADER_ROLE,
        }


if __name__ == "__main__":
    app.run()
