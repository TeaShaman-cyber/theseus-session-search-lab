"""Disposable real PostgreSQL parity probe for the existing Neon projection contract."""
import csv
import json
import io
import os
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile

from session_search.corpus_store import ingest_artifact
from session_search.projected_evidence import (
    capture_local_projection, capture_neon_projection, compare_projected_evidence,
)
from session_search.shard_bundle import build_bundle, verify_bundle
from test_corpus_store import _write_capture, _message

ROOT = Path(__file__).resolve().parents[1]


def sql(statement, *, ok=True):
    result = subprocess.run(["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"],
                            input=statement, text=True, capture_output=True)
    if ok and result.returncode:
        raise RuntimeError(result.stderr)
    if not ok:
        assert result.returncode != 0, "expected SQL rejection"
    return result.stdout.strip()


def literal(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def tuple_sql(values):
    return "(" + ",".join(literal(v) for v in values) + ")"


assert os.environ.get("PGDATABASE") == "ss_projection_contract"
assert sql("SELECT current_database();") == "ss_projection_contract"
assert sql("SELECT to_regnamespace('session_search') IS NULL;") == "t"
for migration in ("001_federated_shard_v1", "002_federated_shard_privileges",
                  "003_federated_generation_lifecycle", "004_arcade_building_loader",
                  "005_ready_marker_capability", "006_activation_marker_capability",
                  "007_safe_message_tsvector", "013_pinned_session_browse"):
    sql((ROOT / "sql/neon" / (migration + ".sql")).read_text())

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    corpus = root / "corpus"
    common = dict(scope="synthetic-corpus", provider="chatgpt", session_id="s",
                  max_events=100, max_bytes=2000)
    original = [_message("m1", "чай 🍵", 3.0, role="user"),
                _message("m2", "чай 🍵", 4.0, role="user"),
                _message(None, "anonymous", 5.0)]
    ingest_artifact(_write_capture(root / "a.zip", "s", original), corpus)
    generations = []
    locals_, clouds = [], []

    def capture(label):
        bundle = root / (label + ".zip")
        receipt = build_bundle(corpus, provider="chatgpt", shard_id="chatgpt-0001",
                               output_path=bundle, require_verified=True)
        assert verify_bundle(bundle) == receipt
        generation = receipt["generation_id"]
        with ZipFile(bundle) as z:
            sessions = list(csv.reader(io.StringIO(z.read("sessions.csv").decode())))[1:]
            messages = list(csv.reader(io.StringIO(z.read("messages.csv").decode())))[1:]
        inserts = [
            "SET ROLE session_search_building_loader;",
            "INSERT INTO session_search.generations(generation_id,shard_id,provider,source_manifest_sha256) VALUES "
            + tuple_sql([generation, receipt["shard_id"], receipt["provider"], receipt["source_manifest_sha256"]]) + ";",
        ]
        for s in sessions:
            inserts.append("INSERT INTO session_search.sessions VALUES " + tuple_sql([generation, *s[:3], s[3] or None, s[4] or None]) + ";")
        for m in messages:
            inserts.append("INSERT INTO session_search.messages(generation_id,session_id,ordinal,message_id,role,search_class,create_time,text) VALUES "
                           + tuple_sql([generation, *m[:5], m[5] or None, m[6]]) + ";")
        sql("BEGIN;" + "\n".join(inserts) + "\nCOMMIT;")

        cursor = None
        def fetch(position, limit, chars):
            nonlocal cursor
            request = dict(generation_id=generation, session_id="s", shard_id="chatgpt-0001",
                           limit=min(limit, 2), max_chars=min(chars, 3))
            if cursor is not None:
                request["cursor"] = cursor
            page = json.loads(subprocess.check_output(
                ["node", str(ROOT / "qa/projected-evidence-router.mjs")],
                input=json.dumps(request), text=True,
            ))
            assert page["generation_binding"] == "router_pinned_v1"
            assert page["unavailable_shards"] == []
            cursor = page["next_cursor"]
            return page

        sql("SET ROLE session_search_reader; SELECT session_search.read_generation_session_v1"
            + tuple_sql([generation, "s", 0, None, 0, 2, 3]) + ";", ok=False)
        sql("SET ROLE session_search_ready_marker; SELECT session_search.mark_generation_ready"
            + tuple_sql([generation, receipt["source_manifest_sha256"], len(sessions), len(messages)]) + ";")
        local = capture_local_projection(corpus, **common)
        cloud = capture_neon_projection(fetch, shard_id="chatgpt-0001", generation_id=generation,
                                        max_pages=100, **common)
        assert local["events"] == cloud["events"]
        assert local["coverage"] == cloud["coverage"]
        assert not compare_projected_evidence(local, cloud)["changed"]
        generations.append(generation)
        locals_.append(local)
        clouds.append(cloud)

    capture("first")
    # Existing anonymous event is preserved: add only identified content in later capture.
    ingest_artifact(_write_capture(root / "b.zip", "s", [
        _message("m1", "чай 🍵", 1.0, role="user"),
        _message("m2", "чай 🍵", 4.0, role="user"),
        _message("m3", "чай 🍵", 6.0, role="user"),
    ], complete=True), corpus)
    capture("second")
    assert generations[0] != generations[1]
    local_delta = compare_projected_evidence(*locals_)
    cloud_delta = compare_projected_evidence(*clouds)
    for key in ("added", "removed", "changed", "order_changed", "coverage_changed", "currentness"):
        assert local_delta[key] == cloud_delta[key], key
    assert local_delta["added"] == ["m3"]
    assert local_delta["changed"] == ["m1"]
    assert not local_delta["removed"]
    # The anonymous event is not covered by the second artifact.
    assert not local_delta["coverage_changed"]
    assert locals_[1]["coverage"] == "PARTIAL_SESSION_SLICE"
    sql("SET ROLE session_search_reader; SELECT text FROM session_search.messages;", ok=False)
    print("PROJECTED_EVIDENCE_POSTGRES_PASS two_generations=true parity=true unicode_paging=true reader_only=true")
