import unittest

from session_search.federation import federated_search


class FederatedSearchTest(unittest.TestCase):
    def test_merges_two_ready_shards_deterministically_with_provenance(self):
        catalog = [
            {
                "shard_id": "chatgpt-0001",
                "provider": "chatgpt",
                "project_id": "project-a",
                "generation_id": "a" * 64,
                "state": "READY",
            },
            {
                "shard_id": "hermes-0001",
                "provider": "hermes",
                "project_id": "project-b",
                "generation_id": "b" * 64,
                "state": "READY",
            },
        ]

        def search_shard(shard, query, scopes, limit):
            self.assertEqual(query, "copper compass")
            self.assertEqual(scopes, ["dialogue", "evidence"])
            if shard["shard_id"] == "chatgpt-0001":
                return {
                    "generation_id": "a" * 64,
                    "hits": [
                        {
                            "session_id": "session-chatgpt",
                            "message_id": "c1",
                            "ordinal": 1,
                            "text": "copper compass from chatgpt",
                        }
                    ],
                }
            return {
                "generation_id": "b" * 64,
                "hits": [
                    {
                        "session_id": "session-hermes",
                        "message_id": "h1",
                        "ordinal": 2,
                        "text": "copper compass from hermes",
                    }
                ],
            }

        result = federated_search(
            catalog,
            search_shard,
            "copper compass",
            ["dialogue", "evidence"],
            10,
        )

        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["queried_shards"], ["chatgpt-0001", "hermes-0001"])
        self.assertEqual(result["unavailable_shards"], [])
        self.assertEqual(
            [(hit["shard_id"], hit["generation_id"]) for hit in result["hits"]],
            [("chatgpt-0001", "a" * 64), ("hermes-0001", "b" * 64)],
        )
        self.assertEqual([hit["shard_rank"] for hit in result["hits"]], [1, 1])

    def test_provider_scope_avoids_querying_unrelated_shards(self):
        catalog = [
            {
                "shard_id": "chatgpt-0001",
                "provider": "chatgpt",
                "project_id": "project-a",
                "generation_id": "a" * 64,
                "state": "READY",
            },
            {
                "shard_id": "hermes-0001",
                "provider": "hermes",
                "project_id": "project-b",
                "generation_id": "b" * 64,
                "state": "READY",
            },
        ]
        calls = []

        def search_shard(shard, query, scopes, limit):
            calls.append(shard["shard_id"])
            return {"generation_id": shard["generation_id"], "hits": []}

        result = federated_search(
            catalog,
            search_shard,
            "needle",
            ["dialogue"],
            8,
            provider="hermes",
        )

        self.assertEqual(calls, ["hermes-0001"])
        self.assertEqual(result["queried_shards"], ["hermes-0001"])
        self.assertEqual(result["status"], "NO_SIGNAL")

    def test_one_failed_shard_returns_partial_with_healthy_hits(self):
        catalog = [
            {
                "shard_id": "chatgpt-0001",
                "provider": "chatgpt",
                "project_id": "project-a",
                "generation_id": "a" * 64,
                "state": "READY",
            },
            {
                "shard_id": "chatgpt-0002",
                "provider": "chatgpt",
                "project_id": "project-b",
                "generation_id": "b" * 64,
                "state": "READY",
            },
        ]

        def search_shard(shard, query, scopes, limit):
            if shard["shard_id"] == "chatgpt-0002":
                raise RuntimeError("synthetic shard unavailable")
            return {
                "generation_id": "a" * 64,
                "hits": [
                    {
                        "session_id": "session-a",
                        "message_id": "a1",
                        "ordinal": 1,
                        "text": "healthy shard hit",
                    }
                ],
            }

        result = federated_search(catalog, search_shard, "healthy", ["dialogue"], 8)

        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual([hit["shard_id"] for hit in result["hits"]], ["chatgpt-0001"])
        self.assertEqual(
            result["unavailable_shards"],
            [{"shard_id": "chatgpt-0002", "reason": "QUERY_FAILED"}],
        )

    def test_non_ready_and_stale_generation_never_become_current_hits(self):
        catalog = [
            {
                "shard_id": "chatgpt-building",
                "provider": "chatgpt",
                "project_id": "project-a",
                "generation_id": "a" * 64,
                "state": "BUILDING",
            },
            {
                "shard_id": "chatgpt-stale",
                "provider": "chatgpt",
                "project_id": "project-b",
                "generation_id": "b" * 64,
                "state": "READY",
            },
        ]
        calls = []

        def search_shard(shard, query, scopes, limit):
            calls.append(shard["shard_id"])
            return {
                "generation_id": "c" * 64,
                "hits": [
                    {
                        "session_id": "stale-session",
                        "message_id": "s1",
                        "ordinal": 1,
                        "text": "must not escape stale generation",
                    }
                ],
            }

        result = federated_search(catalog, search_shard, "stale", ["dialogue"], 8)

        self.assertEqual(calls, ["chatgpt-stale"])
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["hits"], [])
        self.assertEqual(
            result["unavailable_shards"],
            [
                {"shard_id": "chatgpt-building", "reason": "NOT_READY"},
                {"shard_id": "chatgpt-stale", "reason": "STALE_GENERATION"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
