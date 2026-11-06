from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from session_search.refresh_plan import RefreshPlanError, prepare_refresh_plan


SCHEMA = "theseus.session-search-refresh-plan.v1"


def generation_id(source_manifest_sha256: str) -> str:
    return hashlib.sha256(
        ("session-search-generation-v1\n" + source_manifest_sha256).encode("ascii")
    ).hexdigest()


def shard(
    *,
    provider: str,
    shard_id: str,
    source_manifest_sha256: str,
    project: str,
    branch: str,
    previous: str | None,
    sessions: int,
    messages: int,
) -> dict:
    return {
        "provider": provider,
        "shard_id": shard_id,
        "drive_file_id": f"drive_{shard_id.replace('-', '_')}_12345",
        "expected_bundle_sha256": hashlib.sha256(
            ("bundle:" + shard_id).encode("ascii")
        ).hexdigest(),
        "neon_project_id": project,
        "neon_branch_id": branch,
        "neon_database_name": "neondb",
        "generation_id": generation_id(source_manifest_sha256),
        "source_manifest_sha256": source_manifest_sha256,
        "expected_session_count": sessions,
        "expected_message_count": messages,
        "expected_previous_generation_id": previous,
        "serving_url": f"https://{branch}-{shard_id}.example.test/search",
    }


def valid_manifest() -> dict:
    source_a = hashlib.sha256(b"source-a").hexdigest()
    source_b = hashlib.sha256(b"source-b").hexdigest()
    return {
        "schema": SCHEMA,
        "source_commit": "a" * 40,
        "shards": [
            shard(
                provider="chatgpt",
                shard_id="chatgpt-0002",
                source_manifest_sha256=source_b,
                project="project-b",
                branch="br-project-b",
                previous="b" * 64,
                sessions=20,
                messages=200,
            ),
            shard(
                provider="chatgpt",
                shard_id="chatgpt-0001",
                source_manifest_sha256=source_a,
                project="project-a",
                branch="br-project-a",
                previous="c" * 64,
                sessions=10,
                messages=100,
            ),
        ],
    }


class RefreshPlanTest(unittest.TestCase):
    def test_prepare_is_deterministic_under_json_key_and_shard_order(self) -> None:
        left = valid_manifest()
        right = json.loads(json.dumps(left, sort_keys=True))
        right["shards"] = list(reversed(right["shards"]))

        prepared_left = prepare_refresh_plan(left)
        prepared_right = prepare_refresh_plan(right)

        self.assertEqual(prepared_left["plan_digest"], prepared_right["plan_digest"])
        self.assertEqual(prepared_left["actions"], prepared_right["actions"])
        self.assertEqual(
            prepared_left["stage_ready_plan_digest"],
            prepared_right["stage_ready_plan_digest"],
        )
        self.assertEqual(
            prepared_left["stage_ready_plan"],
            prepared_right["stage_ready_plan"],
        )

    def test_prepare_emits_ordered_non_mutating_plan_with_explicit_promotion_boundary(self) -> None:
        prepared = prepare_refresh_plan(valid_manifest())

        self.assertEqual(prepared["schema"], "theseus.session-search-refresh-prepared.v1")
        self.assertEqual(prepared["source_commit"], "a" * 40)
        self.assertEqual(prepared["promotion_boundary"], "EXPLICIT_APPLY_REQUIRED")
        self.assertEqual(
            [(action["stage"], action.get("shard_id")) for action in prepared["actions"]],
            [
                ("stage_bundle_ready", "chatgpt-0001"),
                ("stage_bundle_ready", "chatgpt-0002"),
                ("activate_generation", "chatgpt-0001"),
                ("activate_generation", "chatgpt-0002"),
                ("update_router_catalog", None),
                ("app_smoke", None),
            ],
        )
        self.assertFalse(prepared["mutates_state"])
        self.assertEqual(
            prepared["plan_digest"],
            "c8bc27da48d7b306a227aa1015e59383b2d5d58753bf813c763cb9eb49e73521",
        )

        stage_plan = prepared["stage_ready_plan"]
        self.assertEqual(
            stage_plan["schema"],
            "theseus.session-search-stage-ready-plan.v1",
        )
        self.assertEqual(stage_plan["source_commit"], "a" * 40)
        self.assertEqual(
            stage_plan["parent_plan_digest"],
            prepared["plan_digest"],
        )
        self.assertRegex(prepared["stage_ready_plan_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            [entry["shard_id"] for entry in stage_plan["shards"]],
            ["chatgpt-0001", "chatgpt-0002"],
        )

        stage_entry = stage_plan["shards"][0]
        self.assertEqual(
            set(stage_entry["inputs"]),
            {
                "drive_file_id",
                "expected_bundle_sha256",
                "neon_project_id",
                "neon_branch_id",
                "neon_database_name",
            },
        )
        self.assertEqual(
            set(stage_entry["expected_receipt"]),
            {
                "bundle_sha256",
                "generation_id",
                "source_manifest_sha256",
                "provider",
                "shard_id",
                "session_count",
                "message_count",
                "neon_project_id",
                "neon_branch_id",
                "neon_database_name",
            },
        )
        flattened = json.dumps(stage_plan, sort_keys=True)
        self.assertNotIn("activate_generation", flattened)
        self.assertNotIn("expected_previous_generation_id", flattened)
        self.assertNotIn("serving_url", flattened)
        self.assertNotIn("update_router_catalog", flattened)
        self.assertNotIn("app_smoke", flattened)

        stage = prepared["actions"][0]
        self.assertEqual(
            set(stage["inputs"]),
            {
                "drive_file_id",
                "expected_bundle_sha256",
                "neon_project_id",
                "neon_branch_id",
                "neon_database_name",
            },
        )
        self.assertEqual(stage["shard_id"], "chatgpt-0001")
        self.assertNotIn("generation_id", stage["inputs"])
        self.assertNotIn("expected_session_count", stage["inputs"])
        self.assertNotIn("expected_message_count", stage["inputs"])

        activation = prepared["actions"][2]
        self.assertEqual(activation["stage"], "activate_generation")
        self.assertEqual(
            activation["inputs"]["expected_previous_generation_id"],
            "c" * 64,
        )

    def test_generation_id_must_match_source_manifest_identity(self) -> None:
        manifest = valid_manifest()
        manifest["shards"][0]["generation_id"] = "d" * 64

        with self.assertRaisesRegex(RefreshPlanError, "GENERATION_ID_MISMATCH"):
            prepare_refresh_plan(manifest)

    def test_duplicate_shard_id_fails_closed(self) -> None:
        manifest = valid_manifest()
        manifest["shards"][1]["shard_id"] = manifest["shards"][0]["shard_id"]

        with self.assertRaisesRegex(RefreshPlanError, "DUPLICATE_SHARD_ID"):
            prepare_refresh_plan(manifest)

    def test_duplicate_neon_target_fails_closed(self) -> None:
        manifest = valid_manifest()
        manifest["shards"][1]["neon_project_id"] = manifest["shards"][0]["neon_project_id"]
        manifest["shards"][1]["neon_branch_id"] = manifest["shards"][0]["neon_branch_id"]

        with self.assertRaisesRegex(RefreshPlanError, "DUPLICATE_NEON_TARGET"):
            prepare_refresh_plan(manifest)

    def test_missing_previous_generation_is_not_treated_as_null(self) -> None:
        manifest = valid_manifest()
        del manifest["shards"][0]["expected_previous_generation_id"]

        with self.assertRaisesRegex(RefreshPlanError, "SHARD_KEYS_MISMATCH"):
            prepare_refresh_plan(manifest)

    def test_unknown_fields_fail_closed_instead_of_carrying_secrets(self) -> None:
        manifest = valid_manifest()
        manifest["shards"][0]["database_password"] = "do-not-carry-secrets"

        with self.assertRaisesRegex(RefreshPlanError, "SHARD_KEYS_MISMATCH"):
            prepare_refresh_plan(manifest)

    def test_counts_must_be_positive_integers(self) -> None:
        for field, value in [
            ("expected_session_count", 0),
            ("expected_message_count", -1),
            ("expected_message_count", True),
        ]:
            with self.subTest(field=field, value=value):
                manifest = valid_manifest()
                manifest["shards"][0][field] = value
                with self.assertRaisesRegex(RefreshPlanError, "COUNT_OUT_OF_BOUNDS"):
                    prepare_refresh_plan(manifest)

    def test_serving_url_must_be_https(self) -> None:
        manifest = valid_manifest()
        manifest["shards"][0]["serving_url"] = "http://example.test/search"

        with self.assertRaisesRegex(RefreshPlanError, "SERVING_URL_INVALID"):
            prepare_refresh_plan(manifest)


    def test_prepare_cli_emits_stable_receipt(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = pathlib.Path(tmp) / "refresh.json"
            manifest_path.write_text(
                json.dumps(valid_manifest(), ensure_ascii=False),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(root / "scripts" / "prepare_refresh.py"), "--manifest", str(manifest_path)],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(lines[0], "SESSION_SEARCH_REFRESH_PREPARE_PASS")
        payload = json.loads("\n".join(lines[1:]))
        self.assertEqual(payload["promotion_boundary"], "EXPLICIT_APPLY_REQUIRED")
        self.assertFalse(payload["mutates_state"])
        self.assertRegex(payload["plan_digest"], r"^[0-9a-f]{64}$")

    def test_prepare_cli_rejects_duplicate_json_keys(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        source_manifest = hashlib.sha256(b"duplicate-key-source").hexdigest()
        raw = json.dumps({
            "schema": SCHEMA,
            "source_commit": "a" * 40,
            "shards": [shard(
                provider="chatgpt",
                shard_id="chatgpt-0001",
                source_manifest_sha256=source_manifest,
                project="project-a",
                branch="br-project-a",
                previous="b" * 64,
                sessions=1,
                messages=2,
            )],
        })
        raw = raw.replace(
            '"source_commit": "' + ("a" * 40) + '"',
            '"source_commit": "' + ("a" * 40) + '", "source_commit": "' + ("b" * 40) + '"',
            1,
        )

        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = pathlib.Path(tmp) / "duplicate.json"
            manifest_path.write_text(raw, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(root / "scripts" / "prepare_refresh.py"), "--manifest", str(manifest_path)],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("DUPLICATE_JSON_KEY:source_commit", proc.stderr)


if __name__ == "__main__":
    unittest.main()
