import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from session_search.corpus_store import ingest_artifact
from session_search.projected_evidence import capture_local_projection, capture_neon_projection, compare_projected_evidence
from test_corpus_store import _write_capture, _message


class ProjectedEvidenceTest(unittest.TestCase):
    def test_actual_local_ingest_and_pinned_segmented_neon_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            corpus = root / "corpus"
            ingest_artifact(_write_capture(root / "one.zip", "s", [
                _message("a", "чай 🍵", 1.0, role="user"),
                _message("b", "чай 🍵", 2.0, role="user"),
            ]), corpus)
            local = capture_local_projection(corpus, scope="fixture", provider="chatgpt", session_id="s", max_events=10, max_bytes=100)
            base = dict(status="OK", provider="chatgpt", shard_id="chatgpt-0001",
                        generation_binding="router_pinned_v1", unavailable_shards=[],
                        queried_shards=["chatgpt-0001"],
                        generation_id="a"*64, session_id="s", session_coverage="PARTIAL_SESSION_SLICE")
            def segment(mid, ordinal, text, offset, complete):
                return dict(message_id=mid, ordinal=ordinal, role="user", search_class="dialogue",
                            create_time=float(ordinal+1), text=text, text_offset=offset,
                            text_total_chars=5, complete_message=complete)
            pages = [
                dict(base, messages=[segment("a", 0, "чай", 0, False)], has_more=True,
                     next_position=dict(ordinal=0, message_id="a", offset=3)),
                dict(base, messages=[segment("a", 0, " 🍵", 3, True)], has_more=True,
                     next_position=dict(ordinal=0, message_id="a", offset=0)),
                dict(base, messages=[segment("b", 1, "чай 🍵", 0, True)], has_more=False, next_position=None),
            ]
            def read(pages):
                iterator = iter(pages)
                return capture_neon_projection(lambda *_: next(iterator), scope="fixture", provider="chatgpt",
                    shard_id="chatgpt-0001", generation_id="a"*64, session_id="s",
                    max_events=10, max_bytes=100, max_pages=10)
            neon = read(pages)
            self.assertEqual(local["events"], neon["events"])
            self.assertEqual(local["coverage"], neon["coverage"])
            self.assertEqual(compare_projected_evidence(local, neon)["changed"], [])
            self.assertNotIn("чай", str(neon))
            for field, val in [("generation_id", "b"*64), ("status", "BLOCKED"), ("shard_id", "other")]:
                bad = copy.deepcopy(pages)
                bad[1][field] = val
                with self.assertRaises(ValueError):
                    read(bad)
            bad = copy.deepcopy(pages)
            bad[1]["messages"][0]["text_offset"] = 2
            with self.assertRaises(ValueError):
                read(bad)
            for field, value in [
                ("generation_binding", None), ("unavailable_shards", [{"reason":"failed"}]),
                ("queried_shards", []), ("session_coverage", "COMPLETE_EXPOSED_CONVERSATION"),
            ]:
                bad = copy.deepcopy(pages)
                bad[1][field] = value
                with self.assertRaises(ValueError):
                    read(bad)
            bad = copy.deepcopy(pages[:2])
            bad[1].update(messages=[], has_more=False, next_position=None)
            with self.assertRaisesRegex(ValueError, "PROJECTION_PREMATURE_END"):
                read(bad)
            other = copy.deepcopy(neon)
            other["scope"] = "another-corpus"
            other.pop("snapshot_sha256")
            other["snapshot_sha256"] = hashlib.sha256(json.dumps(
                other, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()).hexdigest()
            with self.assertRaisesRegex(ValueError, "PROJECTION_SCOPE_MISMATCH"):
                compare_projected_evidence(local, other)
            with self.assertRaises(ValueError):
                capture_local_projection(corpus, scope="fixture", provider="chatgpt", session_id="s", max_events=1, max_bytes=100)
            with self.assertRaises(ValueError):
                capture_local_projection(corpus, scope="fixture", provider="xai", session_id="s", max_events=10, max_bytes=100)
