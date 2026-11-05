from __future__ import annotations

import asyncio
import inspect
import sys
import types
import unittest
from pathlib import Path


class _FakeMCPApp:
    def __init__(self, **_kwargs):
        self.registered = []

    def tool(self, **_kwargs):
        def decorator(fn):
            self.registered.append(fn.__name__)
            return fn
        return decorator

    def run(self):
        raise AssertionError("unit test must not start MCP runtime")


class _FakeGoogle:
    def __init__(self, **_kwargs):
        pass


arcade_module = types.ModuleType("arcade_mcp_server")
arcade_module.Context = object
arcade_module.MCPApp = _FakeMCPApp
auth_module = types.ModuleType("arcade_mcp_server.auth")
auth_module.Google = _FakeGoogle
sys.modules["arcade_mcp_server"] = arcade_module
sys.modules["arcade_mcp_server.auth"] = auth_module

ROOT = Path(__file__).resolve().parents[1]
PKG_SRC = ROOT / "arcade" / "session-search-materializer" / "src"
if str(PKG_SRC) not in sys.path:
    sys.path.insert(0, str(PKG_SRC))

from session_search_arcade_materializer import server  # noqa: E402


def materialized(status: str, target_state: str) -> dict:
    return {
        "schema": "theseus.session-search-shard-bundle.v1",
        "bundle_sha256": "a" * 64,
        "generation_id": "b" * 64,
        "source_manifest_sha256": "c" * 64,
        "provider": "chatgpt",
        "shard_id": "chatgpt-0001",
        "session_count": 12,
        "message_count": 345,
        "status": status,
        "target_state": target_state,
        "loaded_sessions": 12,
        "loaded_messages": 345,
        "neon_project_id": "project-a",
        "neon_branch_id": "br-project-a",
        "neon_database_name": "neondb",
        "loader_role": "session_search_arcade_loader_sql",
    }


def ready_receipt() -> dict:
    return {
        "status": "READY",
        "target_state": "READY",
        "generation_id": "b" * 64,
        "source_manifest_sha256": "c" * 64,
        "session_count": 12,
        "message_count": 345,
        "neon_project_id": "project-a",
        "neon_branch_id": "br-project-a",
        "neon_database_name": "neondb",
        "ready_role": "session_search_arcade_ready_sql",
    }


class StageBundleReadyServerTest(unittest.TestCase):
    def test_tool_is_registered_on_maintenance_app(self) -> None:
        self.assertIn("stage_bundle_ready", server.app.registered)

    def test_tool_has_only_bundle_identity_and_target_inputs(self) -> None:
        sig = inspect.signature(server.stage_bundle_ready)
        self.assertEqual(
            list(sig.parameters),
            [
                "context",
                "drive_file_id",
                "expected_bundle_sha256",
                "neon_project_id",
                "neon_branch_id",
                "neon_database_name",
            ],
        )

    def test_tool_composes_materialize_and_ready_without_activation(self) -> None:
        calls = []
        original_materialize = server.materialize_bundle
        original_ready = server.mark_bundle_ready
        original_activate = server.activate_ready_generation

        async def fake_materialize(context, drive_file_id, expected_bundle_sha256,
                                   neon_project_id, neon_branch_id, neon_database_name="neondb"):
            calls.append(("materialize", drive_file_id, expected_bundle_sha256,
                          neon_project_id, neon_branch_id, neon_database_name))
            return materialized("LOADED", "BUILDING")

        async def fake_ready(context, neon_project_id, neon_branch_id, generation_id,
                             expected_source_manifest_sha256, expected_session_count,
                             expected_message_count, neon_database_name="neondb"):
            calls.append(("ready", generation_id, expected_source_manifest_sha256,
                          expected_session_count, expected_message_count,
                          neon_project_id, neon_branch_id, neon_database_name))
            return ready_receipt()

        async def forbidden_activate(*_args, **_kwargs):
            raise AssertionError("stage-to-READY must not activate")

        server.materialize_bundle = fake_materialize
        server.mark_bundle_ready = fake_ready
        server.activate_ready_generation = forbidden_activate
        try:
            result = asyncio.run(
                server.stage_bundle_ready(
                    object(),
                    "drive_chatgpt_0001_12345",
                    "a" * 64,
                    "project-a",
                    "br-project-a",
                    "neondb",
                )
            )
        finally:
            server.materialize_bundle = original_materialize
            server.mark_bundle_ready = original_ready
            server.activate_ready_generation = original_activate

        self.assertEqual(calls[0][0], "materialize")
        self.assertEqual(calls[1], (
            "ready",
            "b" * 64,
            "c" * 64,
            12,
            345,
            "project-a",
            "br-project-a",
            "neondb",
        ))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["promotion_boundary"], "READY_ONLY_STOP")
        self.assertTrue(result["ready_transition_performed"])

    def test_tool_ready_replay_skips_ready_and_activation(self) -> None:
        original_materialize = server.materialize_bundle
        original_ready = server.mark_bundle_ready
        original_activate = server.activate_ready_generation

        async def fake_materialize(*_args, **_kwargs):
            return materialized("ALREADY_READY", "READY")

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("READY replay must not mutate")

        server.materialize_bundle = fake_materialize
        server.mark_bundle_ready = forbidden
        server.activate_ready_generation = forbidden
        try:
            result = asyncio.run(
                server.stage_bundle_ready(
                    object(),
                    "drive_chatgpt_0001_12345",
                    "a" * 64,
                    "project-a",
                    "br-project-a",
                    "neondb",
                )
            )
        finally:
            server.materialize_bundle = original_materialize
            server.mark_bundle_ready = original_ready
            server.activate_ready_generation = original_activate

        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["ready_transition_performed"])


if __name__ == "__main__":
    unittest.main()
