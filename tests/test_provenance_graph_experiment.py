import hashlib
import importlib.util
import json
import pathlib
import sqlite3
import tempfile
import unittest
import zipfile

from session_search.corpus_store import CorpusPaths, ingest_artifact

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECTOR_PATH = ROOT / "experiments" / "002-provenance-graph" / "projector.py"


def _load_projector():
    spec = importlib.util.spec_from_file_location("provenance_graph_projector", PROJECTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("projector import failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _message(mid, text, ts, role="assistant"):
    return {
        "id": mid,
        "author": {"role": role},
        "create_time": ts,
        "content": {"content_type": "text", "parts": [text]},
    }


def _write_capture(path: pathlib.Path, session_id: str, messages: list[dict]) -> pathlib.Path:
    payload = {
        "conversation_id": session_id,
        "title": "KG provenance fixture",
        "page_info": {"has_previous_page": False, "has_next_page": False},
        "messages": messages,
    }
    data = json.dumps(payload, sort_keys=True).encode()
    member = "optional/conversation-1.bin"
    manifest = {
        "schema": "barn-doctor-export:v1",
        "files": [{"name": member, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        zf.writestr(member, data)
    return path


def _db_row(corpus: pathlib.Path, sql: str, params=()):
    with sqlite3.connect(CorpusPaths.from_root(corpus).db) as conn:
        return conn.execute(sql, params).fetchone()


def _file_sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProvenanceGraphExperimentTest(unittest.TestCase):
    def _fixture(self, root: pathlib.Path):
        corpus = root / "corpus"
        capture = _write_capture(
            root / "capture.zip",
            "session-a",
            [_message("m1", "Alice uses Session Search for research.", 1.0, role="user")],
        )
        ingest_artifact(capture, corpus)
        row = _db_row(
            corpus,
            "SELECT canonical_message_sha256,text FROM messages WHERE session_id=? AND message_id=?",
            ("session-a", "m1"),
        )
        self.assertIsNotNone(row)
        return corpus, row[0], row[1]

    def _extracted_claim(self, message_sha: str, text: str):
        evidence = "Alice uses Session Search"
        start = text.index(evidence)
        return {
            "claim_id": "e1",
            "kind": "extracted",
            "subject": {"canonical": "alice", "mention": "Alice", "type": "person"},
            "predicate": "uses",
            "object": {"canonical": "session search", "mention": "Session Search", "type": "technology"},
            "source": {
                "session_id": "session-a",
                "message_id": "m1",
                "canonical_message_sha256": message_sha,
                "evidence": {"start": start, "end": start + len(evidence), "text": evidence},
            },
        }

    def test_valid_projection_binds_immutable_provenance_and_preserves_aliases(self):
        projector = _load_projector()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus, message_sha, text = self._fixture(root)
            db = CorpusPaths.from_root(corpus).db
            before = _file_sha256(db)
            extracted = self._extracted_claim(message_sha, text)
            inferred = {
                "claim_id": "i1",
                "kind": "inferred",
                "subject": {"canonical": "alice", "mention": "Alice", "type": "person"},
                "predicate": "related_to",
                "object": {"canonical": "research", "mention": "research", "type": "concept"},
                "method": "synthetic-rule-v0",
                "source_claim_ids": ["e1"],
            }

            graph_a = projector.project_graph(corpus, [extracted, inferred])
            graph_b = projector.project_graph(corpus, [extracted, inferred])
            self.assertEqual(graph_a, graph_b)
            self.assertEqual(_file_sha256(db), before)
            self.assertEqual(graph_a["schema"], "theseus.session-search-provenance-graph.v0")
            self.assertEqual(graph_a["corpus_verification"], "VERIFIED")

            edges = {edge["claim_id"]: edge for edge in graph_a["edges"]}
            e1 = edges["e1"]
            self.assertEqual(e1["edge_class"], "EXTRACTED_CLAIM")
            self.assertEqual(e1["authority"], "NON_AUTHORITATIVE_PROJECTION")
            self.assertEqual(e1["provenance"]["canonical_message_sha256"], message_sha)
            self.assertTrue(e1["provenance"]["artifact_sha256s"])
            self.assertTrue(e1["provenance"]["source_object_sha256s"])

            i1 = edges["i1"]
            self.assertEqual(i1["edge_class"], "INFERRED_RELATION")
            self.assertEqual(i1["authority"], "NON_AUTHORITATIVE_PROJECTION")
            self.assertEqual(i1["method"], "synthetic-rule-v0")
            self.assertEqual(i1["source_edge_ids"], [e1["edge_id"]])

            nodes = {node["canonical"]: node for node in graph_a["nodes"]}
            self.assertIn("Session Search", nodes["session search"]["aliases"])
            self.assertIn("research", nodes["research"]["aliases"])

    def test_wrong_message_hash_fails_closed(self):
        projector = _load_projector()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus, message_sha, text = self._fixture(root)
            claim = self._extracted_claim(message_sha, text)
            claim["source"]["canonical_message_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "MESSAGE_HASH_MISMATCH"):
                projector.project_graph(corpus, [claim])

    def test_wrong_evidence_span_fails_closed(self):
        projector = _load_projector()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus, message_sha, text = self._fixture(root)
            claim = self._extracted_claim(message_sha, text)
            claim["source"]["evidence"] = {"start": 0, "end": 5, "text": "WRONG"}
            with self.assertRaisesRegex(ValueError, "EVIDENCE_SPAN_MISMATCH"):
                projector.project_graph(corpus, [claim])

    def test_inference_requires_existing_source_claim(self):
        projector = _load_projector()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus, _, _ = self._fixture(root)
            inferred = {
                "claim_id": "i1",
                "kind": "inferred",
                "subject": {"canonical": "alice", "mention": "Alice", "type": "person"},
                "predicate": "related_to",
                "object": {"canonical": "research", "mention": "research", "type": "concept"},
                "method": "synthetic-rule-v0",
                "source_claim_ids": ["missing"],
            }
            with self.assertRaisesRegex(ValueError, "UNKNOWN_INFERENCE_SOURCE"):
                projector.project_graph(corpus, [inferred])


if __name__ == "__main__":
    unittest.main()
