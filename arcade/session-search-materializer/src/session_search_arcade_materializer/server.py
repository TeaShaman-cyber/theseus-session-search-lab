import tempfile
from pathlib import Path
from typing import Annotated, Any

from arcade_mcp_server import Context, MCPApp
from arcade_mcp_server.auth import Google

from session_search_arcade_materializer.bundle_consumer import download_drive_bundle, verify_bundle

DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
app = MCPApp(name="session_search_materializer", version="0.1.0", log_level="INFO")


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


if __name__ == "__main__":
    app.run()
