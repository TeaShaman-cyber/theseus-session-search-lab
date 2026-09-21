#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys

from session_search.corpus_store import CorpusPaths, verify_corpus

SCHEMA = "theseus.session-search-provenance-graph.v0"


def _stable_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_stable_json_bytes(value)).hexdigest()


def _entity(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("ENTITY_INVALID")
    canonical = value.get("canonical")
    mention = value.get("mention")
    entity_type = value.get("type", "unknown")
    if not isinstance(canonical, str) or not canonical.strip():
        raise ValueError("ENTITY_CANONICAL_MISSING")
    if not isinstance(mention, str) or not mention:
        raise ValueError("ENTITY_MENTION_MISSING")
    if not isinstance(entity_type, str) or not entity_type.strip():
        raise ValueError("ENTITY_TYPE_INVALID")
    return {
        "canonical": canonical.strip(),
        "mention": mention,
        "type": entity_type.strip(),
    }


def _node_id(entity: dict) -> str:
    return "node:" + _sha256_json([entity["type"], entity["canonical"]])[:24]


def _edge_id(payload: dict) -> str:
    return "edge:" + _sha256_json(payload)[:24]


def _load_message(conn: sqlite3.Connection, session_id: str, message_id: str) -> sqlite3.Row:
    rows = conn.execute(
        """
        SELECT row_id,session_id,message_id,canonical_message_sha256,text,role,content_type,search_class
        FROM messages WHERE session_id=? AND message_id=?
        """,
        (session_id, message_id),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError("SOURCE_MESSAGE_NOT_FOUND")
    return rows[0]


def _message_provenance(conn: sqlite3.Connection, message_row_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.sha256 AS artifact_sha256,
               p.member_name,
               ms.page_position,
               ms.source_message_id,
               ms.source_object_sha256
        FROM message_sources ms
        JOIN payload_pages p ON p.page_id=ms.page_id
        JOIN artifacts a ON a.artifact_id=p.artifact_id
        WHERE ms.message_row_id=?
        ORDER BY a.sha256,p.member_name,ms.page_position,ms.source_object_sha256
        """,
        (message_row_id,),
    ).fetchall()
    if not rows:
        raise ValueError("PROVENANCE_MISSING")
    return [dict(row) for row in rows]


def _validate_extracted(conn: sqlite3.Connection, claim: dict) -> tuple[dict, dict, dict]:
    subject = _entity(claim.get("subject"))
    obj = _entity(claim.get("object"))
    predicate = claim.get("predicate")
    if not isinstance(predicate, str) or not predicate.strip():
        raise ValueError("PREDICATE_MISSING")
    source = claim.get("source")
    if not isinstance(source, dict):
        raise ValueError("SOURCE_MISSING")
    session_id = source.get("session_id")
    message_id = source.get("message_id")
    expected_sha = source.get("canonical_message_sha256")
    if not isinstance(session_id, str) or not session_id or not isinstance(message_id, str) or not message_id:
        raise ValueError("SOURCE_IDENTITY_MISSING")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise ValueError("MESSAGE_HASH_INVALID")

    message = _load_message(conn, session_id, message_id)
    if message["canonical_message_sha256"] != expected_sha:
        raise ValueError("MESSAGE_HASH_MISMATCH")

    evidence = source.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("EVIDENCE_MISSING")
    start, end, evidence_text = evidence.get("start"), evidence.get("end"), evidence.get("text")
    if not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool):
        raise ValueError("EVIDENCE_SPAN_INVALID")
    text = str(message["text"])
    if start < 0 or end < start or end > len(text) or not isinstance(evidence_text, str):
        raise ValueError("EVIDENCE_SPAN_INVALID")
    if text[start:end] != evidence_text:
        raise ValueError("EVIDENCE_SPAN_MISMATCH")
    if subject["mention"] not in evidence_text or obj["mention"] not in evidence_text:
        raise ValueError("ENTITY_MENTION_OUTSIDE_EVIDENCE")

    source_rows = _message_provenance(conn, int(message["row_id"]))
    provenance = {
        "session_id": session_id,
        "message_id": message_id,
        "canonical_message_sha256": expected_sha,
        "message_role": str(message["role"]),
        "message_search_class": str(message["search_class"]),
        "evidence": {
            "start": start,
            "end": end,
            "text": evidence_text,
            "sha256": hashlib.sha256(evidence_text.encode("utf-8")).hexdigest(),
        },
        "artifact_sha256s": sorted({str(row["artifact_sha256"]) for row in source_rows}),
        "source_object_sha256s": sorted({str(row["source_object_sha256"]) for row in source_rows}),
        "sources": source_rows,
    }
    return subject, obj, provenance


def _claim_id(claim: object) -> str:
    if not isinstance(claim, dict):
        raise ValueError("CLAIM_INVALID")
    claim_id = claim.get("claim_id")
    if not isinstance(claim_id, str) or not claim_id.strip():
        raise ValueError("CLAIM_ID_MISSING")
    return claim_id.strip()


def project_graph(corpus_root: pathlib.Path, claims: list[dict]) -> dict:
    corpus_root = pathlib.Path(corpus_root)
    verification = verify_corpus(corpus_root)
    if verification.get("status") != "VERIFIED":
        raise ValueError("CORPUS_NOT_VERIFIED")
    if not isinstance(claims, list):
        raise ValueError("CLAIMS_INVALID")

    claim_map: dict[str, dict] = {}
    for claim in claims:
        cid = _claim_id(claim)
        if cid in claim_map:
            raise ValueError("DUPLICATE_CLAIM_ID")
        kind = claim.get("kind")
        if kind not in {"extracted", "inferred"}:
            raise ValueError("CLAIM_KIND_UNSUPPORTED")
        claim_map[cid] = claim

    paths = CorpusPaths.from_root(corpus_root)
    conn = sqlite3.connect(paths.db)
    conn.row_factory = sqlite3.Row
    try:
        nodes: dict[tuple[str, str], dict] = {}
        edges_by_claim: dict[str, dict] = {}

        def add_node(entity: dict) -> str:
            key = (entity["type"], entity["canonical"])
            node = nodes.get(key)
            if node is None:
                node = {
                    "node_id": _node_id(entity),
                    "canonical": entity["canonical"],
                    "type": entity["type"],
                    "aliases": set(),
                }
                nodes[key] = node
            node["aliases"].add(entity["mention"])
            return str(node["node_id"])

        # Evidence-bound extracted claims are resolved first, independent of input order.
        for cid in sorted(claim_map):
            claim = claim_map[cid]
            if claim.get("kind") != "extracted":
                continue
            subject, obj, provenance = _validate_extracted(conn, claim)
            subject_id = add_node(subject)
            object_id = add_node(obj)
            core = {
                "claim_id": cid,
                "edge_class": "EXTRACTED_CLAIM",
                "subject_id": subject_id,
                "predicate": str(claim["predicate"]).strip(),
                "object_id": object_id,
                "provenance": provenance,
            }
            edges_by_claim[cid] = {
                **core,
                "edge_id": _edge_id(core),
                "authority": "NON_AUTHORITATIVE_PROJECTION",
            }

        # Resolve inference lineage only after source edges exist; allow DAGs, reject missing/cycles.
        pending = {cid: claim for cid, claim in claim_map.items() if claim.get("kind") == "inferred"}
        while pending:
            progressed = False
            for cid in sorted(list(pending)):
                claim = pending[cid]
                source_claim_ids = claim.get("source_claim_ids")
                method = claim.get("method")
                if not isinstance(method, str) or not method.strip():
                    raise ValueError("INFERENCE_METHOD_MISSING")
                if not isinstance(source_claim_ids, list) or not source_claim_ids or not all(isinstance(x, str) and x for x in source_claim_ids):
                    raise ValueError("INFERENCE_SOURCES_INVALID")
                unknown = [x for x in source_claim_ids if x not in claim_map]
                if unknown:
                    raise ValueError("UNKNOWN_INFERENCE_SOURCE")
                if not all(x in edges_by_claim for x in source_claim_ids):
                    continue
                subject = _entity(claim.get("subject"))
                obj = _entity(claim.get("object"))
                predicate = claim.get("predicate")
                if not isinstance(predicate, str) or not predicate.strip():
                    raise ValueError("PREDICATE_MISSING")
                subject_id = add_node(subject)
                object_id = add_node(obj)
                source_edge_ids = sorted(edges_by_claim[x]["edge_id"] for x in source_claim_ids)
                core = {
                    "claim_id": cid,
                    "edge_class": "INFERRED_RELATION",
                    "subject_id": subject_id,
                    "predicate": predicate.strip(),
                    "object_id": object_id,
                    "method": method.strip(),
                    "source_edge_ids": source_edge_ids,
                }
                edges_by_claim[cid] = {
                    **core,
                    "edge_id": _edge_id(core),
                    "authority": "NON_AUTHORITATIVE_PROJECTION",
                }
                del pending[cid]
                progressed = True
            if not progressed:
                raise ValueError("INFERENCE_CYCLE")

        node_rows = []
        for node in nodes.values():
            node_rows.append({**node, "aliases": sorted(node["aliases"])})
        node_rows.sort(key=lambda row: row["node_id"])
        edge_rows = [edges_by_claim[cid] for cid in sorted(edges_by_claim)]
        evidence_binding = [
            {
                "claim_id": row["claim_id"],
                "canonical_message_sha256": row["provenance"]["canonical_message_sha256"],
                "artifact_sha256s": row["provenance"]["artifact_sha256s"],
                "source_object_sha256s": row["provenance"]["source_object_sha256s"],
                "evidence_sha256": row["provenance"]["evidence"]["sha256"],
            }
            for row in edge_rows
            if row["edge_class"] == "EXTRACTED_CLAIM"
        ]
        normalized_claims = [claim_map[cid] for cid in sorted(claim_map)]
        return {
            "schema": SCHEMA,
            "corpus_verification": "VERIFIED",
            "claims_sha256": _sha256_json(normalized_claims),
            "evidence_binding_sha256": _sha256_json(evidence_binding),
            "authority": {
                "source_history": "SESSION_SEARCH_ACCEPTED_ARTIFACTS",
                "graph": "NON_AUTHORITATIVE_PROJECTION",
                "note": "Edges represent extracted or inferred interpretations; source-message presence is the historical evidence.",
            },
            "nodes": node_rows,
            "edges": edge_rows,
        }
    finally:
        conn.close()


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except Exception as exc:
            raise ValueError(f"CLAIMS_JSONL_INVALID line={lineno}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"CLAIMS_JSONL_INVALID line={lineno}")
        rows.append(value)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Research-only provenance graph projector for Session Search.")
    parser.add_argument("--corpus", type=pathlib.Path, required=True)
    parser.add_argument("--claims", type=pathlib.Path, required=True, help="JSONL extraction/inference records")
    parser.add_argument("--output", type=pathlib.Path, help="write JSON to file; default stdout")
    args = parser.parse_args(argv)
    try:
        graph = project_graph(args.corpus, _read_jsonl(args.claims))
    except Exception as exc:
        parser.exit(1, f"graph projection failed: {exc}\n")
    rendered = json.dumps(graph, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
