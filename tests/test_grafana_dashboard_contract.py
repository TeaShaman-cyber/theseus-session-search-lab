"""Protect the original candidate dashboard-as-code snapshot (issue #109)."""

import json
import unittest
from pathlib import Path


DASHBOARD = (
    Path(__file__).resolve().parents[1]
    / "grafana"
    / "dashboards.v0alpha1.dashboard.grafana.app"
    / "session-search-candidate.json"
)


class DashboardContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resource = json.loads(DASHBOARD.read_text(encoding="utf-8"))
        cls.panels = cls.resource["spec"]["panels"]

    def test_resource_is_portable_and_identified_by_original_uid(self):
        self.assertEqual(self.resource["apiVersion"], "dashboard.grafana.app/v0alpha1")
        self.assertEqual(self.resource["kind"], "Dashboard")
        self.assertEqual(self.resource["metadata"]["name"], "session-search-candidate")
        self.assertNotIn("namespace", self.resource["metadata"])
        self.assertNotIn("annotations", self.resource["metadata"])

    def test_preserves_nine_original_panel_ids_and_names(self):
        expected = [
            "Search status snapshot",
            "Search mean latency",
            "Shard dispatch snapshot",
            "Shard mean latency",
            "Active provenance",
            "Search events over time (TraceQL)",
            "Search latency p50/p95 (TraceQL)",
            "Shard dispatch events over time (TraceQL)",
            "Shard dispatch latency p50/p95 (TraceQL)",
        ]
        self.assertEqual([p["id"] for p in self.panels], list(range(1, 10)))
        self.assertEqual([p["title"] for p in self.panels], expected)

    def test_mimir_and_tempo_sources_remain_distinct(self):
        for p in self.panels[:5]:
            self.assertEqual(p["datasource"]["uid"], "grafanacloud-prom")
            self.assertEqual(len(p["targets"]), 1)
            self.assertTrue(p["targets"][0]["expr"])
        for p in self.panels[5:]:
            self.assertEqual(p["datasource"]["uid"], "grafanacloud-traces")
            self.assertEqual(len(p["targets"]), 1)
            self.assertEqual(p["targets"][0]["queryType"], "traceql")
            self.assertTrue(p["targets"][0]["query"])

    def test_candidate_plane_is_not_silently_broadened(self):
        for p in self.panels:
            query = p["targets"][0].get("expr") or p["targets"][0].get("query")
            self.assertIn("candidate", query, p["title"])
        self.assertEqual(self.resource["spec"]["title"], "Session Search — candidate canary")


if __name__ == "__main__":
    unittest.main()
