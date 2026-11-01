import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "sql" / "neon" / "001_federated_shard_v1.sql"
PRIVILEGES = ROOT / "sql" / "neon" / "002_federated_shard_privileges.sql"
RETRIEVAL_POLICY = ROOT / "sql" / "neon" / "008_retrieval_policy.sql"
LIFECYCLE = ROOT / "sql" / "neon" / "003_federated_generation_lifecycle.sql"
BUILDING_LOADER = ROOT / "sql" / "neon" / "004_arcade_building_loader.sql"
READY_MARKER = ROOT / "sql" / "neon" / "005_ready_marker_capability.sql"
ACTIVATION_MARKER = ROOT / "sql" / "neon" / "006_activation_marker_capability.sql"
FTS_GUARD = ROOT / "sql" / "neon" / "007_safe_message_tsvector.sql"


class NeonFederatedContractTest(unittest.TestCase):
    def test_retrieval_policy_migration_adds_versioned_strict_and_recall_query_plane(self):
        text = RETRIEVAL_POLICY.read_text()
        self.assertIn("CREATE FUNCTION session_search.search_generation_v2", text)
        self.assertIn("CREATE FUNCTION session_search.search_v2", text)
        self.assertIn("search_mode", text)
        self.assertIn("plainto_tsquery", text)
        self.assertIn("tsvector_to_array", text)
        self.assertIn("' | '", text)
        self.assertIn("GRANT EXECUTE ON FUNCTION session_search.search_v2", text)

    def test_query_plane_migration_exposes_generation_scoped_read_only_surface(self):
        text = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("CREATE SCHEMA session_search", text)
        self.assertIn("CREATE TABLE session_search.generations", text)
        self.assertIn("CREATE TABLE session_search.sessions", text)
        self.assertIn("CREATE TABLE session_search.messages", text)
        self.assertIn("CREATE TABLE session_search.active_generation", text)
        self.assertIn("CREATE FUNCTION session_search.mark_generation_ready", text)
        self.assertIn("CREATE FUNCTION session_search.activate_generation", text)
        self.assertIn("CREATE FUNCTION session_search.search_generation", text)
        self.assertIn("CREATE FUNCTION session_search.search(", text)
        self.assertIn("CREATE FUNCTION session_search.generation_status", text)
        self.assertIn("search generation not READY", text)
        self.assertIn("query text out of bounds", text)
        self.assertIn("result limit out of bounds", text)
        self.assertIn("GENERATED ALWAYS AS", text)
        self.assertIn("USING GIN", text)

    def test_message_fts_projection_uses_safe_guard(self):
        schema = SCHEMA.read_text(encoding="utf-8")
        guard = FTS_GUARD.read_text(encoding="utf-8") if FTS_GUARD.exists() else ""
        self.assertIn("session_search.safe_message_search_vector", schema)
        self.assertIn("EXCEPTION WHEN program_limit_exceeded", guard)
        self.assertIn("candidate := left(candidate", guard)
        self.assertIn("GENERATED ALWAYS AS (session_search.safe_message_search_vector(text)) STORED", schema)

    def test_privilege_migration_keeps_reader_behind_bounded_functions(self):
        text = PRIVILEGES.read_text(encoding="utf-8")
        self.assertIn("CREATE ROLE session_search_owner NOLOGIN", text)
        self.assertIn("CREATE ROLE session_search_reader NOLOGIN", text)
        self.assertIn("CREATE ROLE session_search_materializer NOLOGIN", text)
        self.assertIn("REVOKE ALL ON ALL TABLES IN SCHEMA session_search FROM PUBLIC", text)
        self.assertIn("GRANT EXECUTE ON FUNCTION session_search.search(", text)
        self.assertIn("TO session_search_reader", text)
        self.assertNotIn("GRANT SELECT ON ALL TABLES IN SCHEMA session_search TO session_search_reader", text)

    def test_arcade_building_loader_migration_is_insert_only(self):
        text = BUILDING_LOADER.read_text(encoding="utf-8")
        self.assertIn("CREATE ROLE session_search_building_loader NOLOGIN", text)
        self.assertIn("GRANT SELECT,INSERT ON session_search.generations", text)
        self.assertIn("GRANT SELECT,INSERT ON session_search.sessions", text)
        self.assertIn("GRANT SELECT,INSERT ON session_search.messages", text)
        self.assertNotIn("mark_generation_ready", text)
        self.assertNotIn("activate_generation", text)
        self.assertNotIn("UPDATE", text)
        self.assertNotIn("DELETE", text)

    def test_ready_marker_capability_can_only_mark_ready(self):
        text = READY_MARKER.read_text(encoding="utf-8")
        self.assertIn("CREATE ROLE session_search_ready_marker NOLOGIN", text)
        self.assertIn("GRANT USAGE ON SCHEMA session_search TO session_search_ready_marker", text)
        self.assertIn(
            "GRANT EXECUTE ON FUNCTION session_search.mark_generation_ready(text,text,integer,integer) "
            "TO session_search_ready_marker",
            text,
        )
        self.assertNotIn("activate_generation", text)
        self.assertNotIn("GRANT SELECT", text)
        self.assertNotIn("GRANT INSERT", text)
        self.assertNotIn("GRANT UPDATE", text)
        self.assertNotIn("GRANT DELETE", text)

    def test_activation_marker_capability_can_only_activate(self):
        text = ACTIVATION_MARKER.read_text(encoding="utf-8")
        self.assertIn("CREATE ROLE session_search_activation_marker NOLOGIN", text)
        self.assertIn("GRANT USAGE ON SCHEMA session_search TO session_search_activation_marker", text)
        self.assertIn(
            "GRANT EXECUTE ON FUNCTION session_search.activate_generation(text,text) "
            "TO session_search_activation_marker",
            text,
        )
        self.assertNotIn("mark_generation_ready", text)
        self.assertNotIn("GRANT SELECT", text)
        self.assertNotIn("GRANT INSERT", text)
        self.assertNotIn("GRANT UPDATE", text)
        self.assertNotIn("GRANT DELETE", text)

    def test_lifecycle_migration_makes_ready_generations_immutable(self):
        text = LIFECYCLE.read_text(encoding="utf-8")
        self.assertIn("reject_direct_ready_generation", text)
        self.assertIn("generation insert must start BUILDING", text)
        self.assertIn("guard_projection_mutation", text)
        self.assertIn("projection generation is immutable after READY", text)
        self.assertIn("child generation_id is immutable", text)
        self.assertIn("BEFORE INSERT OR UPDATE OR DELETE ON session_search.sessions", text)
        self.assertIn("BEFORE INSERT OR UPDATE OR DELETE ON session_search.messages", text)


if __name__ == "__main__":
    unittest.main()
