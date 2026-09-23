import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock
import zipfile

from session_search.artifact import normalize_artifact


class ChatGPTExportAdapterTest(unittest.TestCase):
    def message(self, mid, role, content_type, parts, create_time):
        return {
            "id": mid,
            "author": {"role": role, "name": None, "metadata": {}},
            "create_time": create_time,
            "update_time": None,
            "content": {"content_type": content_type, "parts": parts},
            "status": "finished_successfully",
            "end_turn": True,
            "weight": 1.0,
            "metadata": {},
            "recipient": "all",
            "channel": None,
        }

    def node(self, node_id, parent, message):
        return {"id": node_id, "message": message, "parent": parent}

    def conversation(self, cid="conv-1", current="b", mapping=None, title="Synthetic ChatGPT"):
        if mapping is None:
            mapping = {
                "r": self.node("r", None, None),
                "u": self.node("u", "r", self.message("m-u", "user", "text", ["question"], 10.0)),
                "a": self.node("a", "u", self.message("m-a", "assistant", "text", ["answer A"], 20.0)),
                "b": self.node("b", "u", self.message("m-b", "assistant", "text", ["answer B"], 30.0)),
            }
        return {
            "title": title,
            "create_time": 1.0,
            "update_time": 40.0,
            "mapping": mapping,
            "moderation_results": [],
            "current_node": current,
            "conversation_id": cid,
            "id": cid,
        }

    def write_export(self, root: pathlib.Path, conversations, split=False):
        source = root / "chatgpt-export.zip"
        with zipfile.ZipFile(source, "w") as zf:
            if split:
                midpoint = max(1, len(conversations) // 2)
                zf.writestr("conversations-000.json", json.dumps(conversations[:midpoint]))
                zf.writestr("conversations-001.json", json.dumps(conversations[midpoint:]))
            else:
                zf.writestr("conversations-000.json", json.dumps(conversations))
            zf.writestr("chat.html", "<html>ignored</html>")
        return source

    def payload(self, artifact_path):
        with zipfile.ZipFile(artifact_path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            member = manifest["files"][0]["name"]
            return manifest, json.loads(zf.read(member))

    def write_raw_capture(self, path, session_id, messages, title="Synthetic ChatGPT"):
        payload = {
            "conversation_id": session_id,
            "title": title,
            "page_info": {"has_previous_page": True, "has_next_page": False},
            "messages": messages,
        }
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        member = "optional/conversation-raw.bin"
        manifest = {
            "schema": "barn-doctor-export:v1",
            "files": [{"name": member, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
        }
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            zf.writestr(member, data)
        return path

    def test_parent_graph_materializes_every_leaf_and_current_does_not_define_base_identity(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            outputs = materialize_export(self.write_export(root, [self.conversation(current="b")]), root / "out")
            self.assertEqual(len(outputs), 2)
            artifacts = [normalize_artifact(path) for path in outputs]
            by_id = {artifact.session_id: artifact for artifact in artifacts}
            self.assertIn("conv-1", by_id)
            branch_ids = [sid for sid in by_id if sid.startswith("conv-1~branch-")]
            self.assertEqual(len(branch_ids), 1)

            base_dialogue = [m.text for m in by_id["conv-1"].messages if m.search_class == "dialogue"]
            alt_dialogue = [m.text for m in by_id[branch_ids[0]].messages if m.search_class == "dialogue"]
            self.assertEqual(base_dialogue, ["question", "answer A"])
            self.assertEqual(alt_dialogue, ["question", "answer B"])

            manifests = [self.payload(path)[0] for path in outputs]
            current = [m for m in manifests if m["selected_is_current"]]
            self.assertEqual(len(current), 1)
            self.assertTrue(str(current[0]["session_id"]).startswith("conv-1~branch-"))

    def test_switching_current_node_keeps_branch_session_id_set_stable(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first = self.write_export(root, [self.conversation(current="b")])
            first_ids = {normalize_artifact(path).session_id for path in materialize_export(first, root / "out1")}

            second = self.write_export(root, [self.conversation(current="a")])
            second_ids = {normalize_artifact(path).session_id for path in materialize_export(second, root / "out2")}
            self.assertEqual(first_ids, second_ids)

    def test_multimodal_text_stays_dialogue_and_nontext_parts_become_trace(self):
        from session_search.chatgpt_export import materialize_export

        mapping = {
            "r": self.node("r", None, None),
            "u": self.node(
                "u",
                "r",
                self.message(
                    "m-u",
                    "user",
                    "multimodal_text",
                    ["caption", {"content_type": "audio_transcription", "text": "spoken words"}, {"content_type": "image_asset_pointer", "asset_pointer": "file-synthetic"}],
                    10.0,
                ),
            ),
            "a": self.node("a", "u", self.message("m-a", "assistant", "text", ["answer"], 20.0)),
        }
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            artifact = normalize_artifact(
                materialize_export(
                    self.write_export(root, [self.conversation(cid="multi", current="a", mapping=mapping)]),
                    root / "out",
                )[0]
            )
            by_type = {m.content_type: m for m in artifact.messages}
            self.assertEqual([m.text for m in artifact.messages if m.search_class == "dialogue"], ["caption\nspoken words", "answer"])
            self.assertIn("chatgpt_multimodal_trace", by_type)
            self.assertEqual(by_type["chatgpt_multimodal_trace"].search_class, "trace")

    def test_linked_multimodal_projection_reconciles_with_raw_capture_in_both_orders(self):
        from session_search.chatgpt_export import materialize_export
        from session_search.corpus_store import CorpusPaths, ingest_artifact, rebuild_corpus, verify_corpus

        raw_message = self.message(
            "m-u",
            "user",
            "multimodal_text",
            [
                "caption",
                {"content_type": "audio_transcription", "text": "spoken words"},
                {"content_type": "image_asset_pointer", "asset_pointer": "file-synthetic"},
            ],
            10.0,
        )
        mapping = {
            "r": self.node("r", None, None),
            "u": self.node("u", "r", raw_message),
            "a": self.node("a", "u", self.message("m-a", "assistant", "text", ["answer"], 20.0)),
        }
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            projected = materialize_export(
                self.write_export(root, [self.conversation(cid="multi-reconcile", current="a", mapping=mapping)]),
                root / "projected",
            )[0]
            raw = self.write_raw_capture(root / "raw.zip", "multi-reconcile", [raw_message])

            projected_artifact = normalize_artifact(projected)
            raw_artifact = normalize_artifact(raw)
            projected_message = next(m for m in projected_artifact.messages if m.message_id == "m-u")
            raw_normalized = next(m for m in raw_artifact.messages if m.message_id == "m-u")
            self.assertEqual(projected_message.projection_source_canonical_sha256, raw_normalized.canonical_message_sha256)

            for label, order in (("raw-first", (raw, projected)), ("projection-first", (projected, raw))):
                corpus = root / f"corpus-{label}"
                for artifact in order:
                    self.assertEqual(ingest_artifact(artifact, corpus)["status"], "INGESTED")
                self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")
                db = CorpusPaths.from_root(corpus).db
                with sqlite3.connect(db) as conn:
                    row = conn.execute(
                        "SELECT content_type,search_class,text FROM messages WHERE session_id=? AND message_id=?",
                        ("multi-reconcile", "m-u"),
                    ).fetchone()
                    self.assertEqual(row, ("text", "dialogue", "caption\nspoken words"))
                    self.assertEqual(conn.execute(
                        "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?", ("caption",)
                    ).fetchone()[0], 1)
                self.assertEqual(rebuild_corpus(corpus)["status"], "REBUILT")
                self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

    def test_branched_raw_capture_routes_to_alternate_in_both_ingest_orders(self):
        from session_search.chatgpt_export import ingest_export, materialize_export
        from session_search.corpus_store import (
            CorpusPaths,
            ingest_artifact,
            semantic_snapshot,
            verify_corpus,
        )

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(root, [self.conversation(cid="route-alt")])
            children = materialize_export(source, root / "children")
            by_id = {normalize_artifact(path).session_id: path for path in children}
            alt_id = next(sid for sid in by_id if sid.startswith("route-alt~branch-"))
            raw = self.write_raw_capture(
                root / "raw-alt.zip",
                "route-alt",
                [
                    self.message("m-u", "user", "text", ["question"], 10.0),
                    self.message("m-b", "assistant", "text", ["answer B"], 30.0),
                    self.message(
                        "provider-extra", "assistant", "text", ["provider only"], 35.0
                    ),
                ],
            )
            raw_sha = normalize_artifact(raw).artifact_sha256
            snapshots = []

            for label, official_first in (
                ("raw-first", False),
                ("official-first", True),
            ):
                corpus = root / f"corpus-{label}"
                with mock.patch(
                    "session_search.corpus_store._utc_now",
                    return_value="2026-09-23T00:00:00+00:00",
                ):
                    if official_first:
                        self.assertEqual(
                            ingest_export(source, corpus)["status"], "COMPLETE"
                        )
                        self.assertEqual(
                            ingest_artifact(raw, corpus)["status"], "INGESTED"
                        )
                    else:
                        self.assertEqual(
                            ingest_artifact(raw, corpus)["status"], "INGESTED"
                        )
                        self.assertEqual(
                            ingest_export(source, corpus)["status"], "COMPLETE"
                        )
                self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")
                with sqlite3.connect(CorpusPaths.from_root(corpus).db) as conn:
                    route = conn.execute(
                        """
                        SELECT r.projected_session_id,r.route_state
                        FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id
                        WHERE a.sha256=?
                        """,
                        (raw_sha,),
                    ).fetchone()
                    self.assertEqual(route, (alt_id, "BRANCH_MATCH"))
                    self.assertEqual(
                        conn.execute(
                            "SELECT session_id FROM messages WHERE message_id='provider-extra'"
                        ).fetchone()[0],
                        alt_id,
                    )
                snapshots.append(semantic_snapshot(corpus))
            self.assertEqual(snapshots[0], snapshots[1])

    def test_branched_raw_capture_routes_to_base_from_base_only_evidence(self):
        from session_search.chatgpt_export import ingest_export
        from session_search.corpus_store import CorpusPaths, ingest_artifact

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(root, [self.conversation(cid="route-base")])
            corpus = root / "corpus"
            self.assertEqual(ingest_export(source, corpus)["status"], "COMPLETE")
            raw = self.write_raw_capture(
                root / "raw-base.zip",
                "route-base",
                [self.message("m-a", "assistant", "text", ["answer A"], 20.0)],
            )
            raw_sha = normalize_artifact(raw).artifact_sha256
            self.assertEqual(ingest_artifact(raw, corpus)["status"], "INGESTED")
            with sqlite3.connect(CorpusPaths.from_root(corpus).db) as conn:
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT r.projected_session_id,r.route_state
                        FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id
                        WHERE a.sha256=?
                        """,
                        (raw_sha,),
                    ).fetchone(),
                    ("route-base", "BRANCH_MATCH"),
                )

    def test_common_only_and_zero_overlap_raw_captures_are_isolated(self):
        from session_search.chatgpt_export import ingest_export
        from session_search.corpus_store import (
            CorpusPaths,
            ingest_artifact,
            verify_corpus,
        )

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(
                root, [self.conversation(cid="route-unresolved")]
            )
            corpus = root / "corpus"
            self.assertEqual(ingest_export(source, corpus)["status"], "COMPLETE")
            raws = [
                self.write_raw_capture(
                    root / "common.zip",
                    "route-unresolved",
                    [self.message("m-u", "user", "text", ["question"], 10.0)],
                ),
                self.write_raw_capture(
                    root / "zero.zip",
                    "route-unresolved",
                    [
                        self.message(
                            "provider-only",
                            "assistant",
                            "text",
                            ["not in export"],
                            50.0,
                        )
                    ],
                ),
            ]
            observed = []
            for raw in raws:
                raw_artifact = normalize_artifact(raw)
                result = ingest_artifact(raw, corpus)
                self.assertEqual(result["route_state"], "UNRESOLVED")
                self.assertTrue(
                    result["projected_session_id"].startswith(
                        "route-unresolved~unresolved-"
                    )
                )
                observed.append(
                    (raw_artifact.artifact_sha256, result["projected_session_id"])
                )
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")
            self.assertEqual(len({session_id for _sha, session_id in observed}), 2)
            with sqlite3.connect(CorpusPaths.from_root(corpus).db) as conn:
                for sha, projected in observed:
                    self.assertEqual(
                        conn.execute(
                            """
                            SELECT r.projected_session_id,r.route_state
                            FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id
                            WHERE a.sha256=?
                            """,
                            (sha,),
                        ).fetchone(),
                        (projected, "UNRESOLVED"),
                    )

    def test_cross_branch_raw_evidence_fails_without_accepting_artifact(self):
        from session_search.chatgpt_export import ingest_export
        from session_search.corpus_store import (
            CorpusPaths,
            ingest_artifact,
            read_accepted_ledger,
            verify_corpus,
        )

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(root, [self.conversation(cid="route-conflict")])
            corpus = root / "corpus"
            self.assertEqual(ingest_export(source, corpus)["status"], "COMPLETE")
            conflicting = self.write_raw_capture(
                root / "conflict.zip",
                "route-conflict",
                [
                    self.message("m-a", "assistant", "text", ["answer A"], 20.0),
                    self.message("m-b", "assistant", "text", ["answer B"], 30.0),
                ],
            )
            sha = normalize_artifact(conflicting).artifact_sha256
            with self.assertRaisesRegex(
                RuntimeError, "FAILED_BRANCH_RECONCILIATION_CONFLICT"
            ):
                ingest_artifact(conflicting, corpus)
            self.assertNotIn(sha, read_accepted_ledger(CorpusPaths.from_root(corpus)))
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

    def test_single_child_from_branched_snapshot_fails_closed(self):
        from session_search.chatgpt_export import materialize_export
        from session_search.corpus_store import (
            CorpusPaths,
            ingest_artifact,
            read_accepted_ledger,
        )

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            children = materialize_export(
                self.write_export(root, [self.conversation(cid="route-incomplete")]),
                root / "children",
            )
            corpus = root / "corpus"
            with self.assertRaisesRegex(
                RuntimeError, "incomplete ChatGPT branch family"
            ):
                ingest_artifact(children[0], corpus)
            self.assertEqual(read_accepted_ledger(CorpusPaths.from_root(corpus)), {})

    def test_rebuild_repairs_legacy_flat_projection_without_rewriting_ledger_identity(
        self,
    ):
        from session_search.branch_routing import ProjectionRoute
        from session_search.chatgpt_export import materialize_export
        from session_search.corpus_store import (
            CorpusPaths,
            ingest_reconciled_many,
            read_accepted_ledger,
            rebuild_corpus,
            verify_corpus,
        )

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(root, [self.conversation(cid="route-repair")])
            children = materialize_export(source, root / "children")
            alt_id = next(
                normalize_artifact(path).session_id
                for path in children
                if normalize_artifact(path).session_id.startswith(
                    "route-repair~branch-"
                )
            )
            raw = self.write_raw_capture(
                root / "raw.zip",
                "route-repair",
                [self.message("m-b", "assistant", "text", ["answer B"], 30.0)],
            )
            raw_sha = normalize_artifact(raw).artifact_sha256

            def all_direct(_paths, entries):
                return {
                    sha: ProjectionRoute(
                        str(entry["session_id"]),
                        str(entry["session_id"]),
                        "DIRECT",
                        "legacy_flat_projection",
                    )
                    for sha, entry in entries.items()
                }

            corpus = root / "corpus"
            with mock.patch(
                "session_search.corpus_store._plan_projection_routes_for_entries",
                side_effect=all_direct,
            ):
                self.assertEqual(
                    ingest_reconciled_many([raw, *children], corpus)["status"],
                    "COMPLETE",
                )
            before_ledger = read_accepted_ledger(CorpusPaths.from_root(corpus))
            self.assertEqual(before_ledger[raw_sha]["session_id"], "route-repair")
            self.assertEqual(verify_corpus(corpus)["status"], "RECONCILIATION_REQUIRED")
            self.assertEqual(rebuild_corpus(corpus)["status"], "REBUILT")
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")
            after_ledger = read_accepted_ledger(CorpusPaths.from_root(corpus))
            self.assertEqual(after_ledger, before_ledger)
            with sqlite3.connect(CorpusPaths.from_root(corpus).db) as conn:
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT r.projected_session_id,r.route_state,a.session_id
                        FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id
                        WHERE a.sha256=?
                        """,
                        (raw_sha,),
                    ).fetchone(),
                    (alt_id, "BRANCH_MATCH", "route-repair"),
                )

    def test_ledger_ahead_crash_state_rebuilds_to_same_branch_route(self):
        import shutil

        from session_search.chatgpt_export import ingest_export
        from session_search.corpus_store import (
            CorpusPaths,
            _accepted_entry_for_artifact,
            read_accepted_ledger,
            rebuild_corpus,
            verify_corpus,
            write_accepted_entry,
        )

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(
                root, [self.conversation(cid="route-crash-recovery")]
            )
            corpus = root / "corpus"
            self.assertEqual(ingest_export(source, corpus)["status"], "COMPLETE")
            raw = self.write_raw_capture(
                root / "raw-crash.zip",
                "route-crash-recovery",
                [self.message("m-b", "assistant", "text", ["answer B"], 30.0)],
            )
            artifact = normalize_artifact(raw)
            paths = CorpusPaths.from_root(corpus)
            paths.ensure_layout()
            shutil.copyfile(
                raw,
                paths.artifacts_sha256 / f"{artifact.artifact_sha256}.zip",
            )
            write_accepted_entry(
                paths,
                _accepted_entry_for_artifact(
                    artifact, "2026-09-23T00:00:00+00:00"
                ),
            )
            before_ledger = read_accepted_ledger(paths)

            self.assertEqual(verify_corpus(corpus)["status"], "RECONCILIATION_REQUIRED")
            self.assertEqual(rebuild_corpus(corpus)["status"], "REBUILT")
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")
            self.assertEqual(read_accepted_ledger(paths), before_ledger)

            with sqlite3.connect(paths.db) as conn:
                route = conn.execute(
                    """
                    SELECT r.projected_session_id,r.route_state,a.session_id
                    FROM artifact_routes r JOIN artifacts a ON a.artifact_id=r.artifact_id
                    WHERE a.sha256=?
                    """,
                    (artifact.artifact_sha256,),
                ).fetchone()
                self.assertIsNotNone(route)
                self.assertEqual(route[1:], ("BRANCH_MATCH", "route-crash-recovery"))
                self.assertTrue(
                    route[0].startswith("route-crash-recovery~branch-")
                )

    def test_reconciled_publish_failure_rolls_back_new_ledger_entries(self):
        import os

        from session_search.chatgpt_export import ingest_export
        from session_search.corpus_store import (
            CorpusPaths,
            ingest_artifact,
            read_accepted_ledger,
            semantic_snapshot,
            verify_corpus,
        )

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(
                root, [self.conversation(cid="route-publish-failure")]
            )
            corpus = root / "corpus"
            self.assertEqual(ingest_export(source, corpus)["status"], "COMPLETE")
            paths = CorpusPaths.from_root(corpus)
            before_ledger = read_accepted_ledger(paths)
            before_snapshot = semantic_snapshot(corpus)
            raw = self.write_raw_capture(
                root / "raw-publish-failure.zip",
                "route-publish-failure",
                [self.message("m-b", "assistant", "text", ["answer B"], 30.0)],
            )
            raw_sha = normalize_artifact(raw).artifact_sha256
            real_replace = os.replace

            def fail_final_projection_publish(source_path, target_path):
                if pathlib.Path(target_path) == paths.db and pathlib.Path(
                    source_path
                ).name.startswith("reconciled-"):
                    raise OSError("synthetic projection publication failure")
                return real_replace(source_path, target_path)

            with mock.patch(
                "session_search.corpus_store.os.replace",
                side_effect=fail_final_projection_publish,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "reconciled ingest publication failed"
                ):
                    ingest_artifact(raw, corpus)

            self.assertNotIn(raw_sha, read_accepted_ledger(paths))
            self.assertEqual(read_accepted_ledger(paths), before_ledger)
            self.assertEqual(semantic_snapshot(corpus), before_snapshot)
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

    def test_multimodal_projection_lineage_can_discriminate_branch(self):
        from session_search.chatgpt_export import ingest_export
        from session_search.corpus_store import CorpusPaths, ingest_artifact

        raw_message = self.message(
            "m-mm",
            "user",
            "multimodal_text",
            [
                "branch caption",
                {"content_type": "audio_transcription", "text": "branch audio"},
                {"content_type": "image_asset_pointer", "asset_pointer": "file-branch"},
            ],
            30.0,
        )
        mapping = {
            "r": self.node("r", None, None),
            "u": self.node(
                "u", "r", self.message("m-u", "user", "text", ["question"], 10.0)
            ),
            "a": self.node(
                "a",
                "u",
                self.message("m-a", "assistant", "text", ["base answer"], 20.0),
            ),
            "mm": self.node("mm", "u", raw_message),
        }
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(
                root,
                [
                    self.conversation(
                        cid="route-multimodal", current="mm", mapping=mapping
                    )
                ],
            )
            corpus = root / "corpus"
            self.assertEqual(ingest_export(source, corpus)["status"], "COMPLETE")
            raw = self.write_raw_capture(
                root / "raw-mm.zip", "route-multimodal", [raw_message]
            )
            raw_sha = normalize_artifact(raw).artifact_sha256
            result = ingest_artifact(raw, corpus)
            self.assertEqual(result["route_state"], "BRANCH_MATCH")
            self.assertTrue(
                result["projected_session_id"].startswith("route-multimodal~branch-")
            )
            with sqlite3.connect(CorpusPaths.from_root(corpus).db) as conn:
                self.assertEqual(
                    conn.execute(
                        """
                        SELECT r.route_state FROM artifact_routes r
                        JOIN artifacts a ON a.artifact_id=r.artifact_id WHERE a.sha256=?
                        """,
                        (raw_sha,),
                    ).fetchone()[0],
                    "BRANCH_MATCH",
                )

    def test_multimodal_projection_must_match_traced_source_content(self):
        from session_search.chatgpt_export import materialize_export

        mapping = {
            "r": self.node("r", None, None),
            "u": self.node(
                "u",
                "r",
                self.message(
                    "m-u",
                    "user",
                    "multimodal_text",
                    ["caption", {"content_type": "image_asset_pointer", "asset_pointer": "file-a"}],
                    10.0,
                ),
            ),
            "a": self.node("a", "u", self.message("m-a", "assistant", "text", ["answer"], 20.0)),
        }
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            child = materialize_export(
                self.write_export(root, [self.conversation(cid="projection-tamper", current="a", mapping=mapping)]),
                root / "out",
            )[0]
            manifest, payload = self.payload(child)
            projected = next(m for m in payload["messages"] if (m.get("metadata") or {}).get("chatgpt_projection") == "multimodal-text")
            projected["content"]["parts"] = ["unrelated searchable text"]
            member = manifest["files"][0]["name"]
            data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            manifest["files"][0]["bytes"] = len(data)
            manifest["files"][0]["sha256"] = hashlib.sha256(data).hexdigest()
            tampered = root / "tampered.zip"
            with zipfile.ZipFile(tampered, "w") as zf:
                zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
                zf.writestr(member, data)
            with self.assertRaisesRegex(ValueError, "multimodal projection .*mismatch"):
                normalize_artifact(tampered)

    def test_identical_text_projections_with_different_multimodal_lineage_conflict(self):
        from session_search.chatgpt_export import materialize_export
        from session_search.corpus_store import ingest_artifact, verify_corpus

        def conversation(asset_pointer):
            mapping = {
                "r": self.node("r", None, None),
                "u": self.node(
                    "u",
                    "r",
                    self.message(
                        "m-u",
                        "user",
                        "multimodal_text",
                        ["same caption", {"content_type": "image_asset_pointer", "asset_pointer": asset_pointer}],
                        10.0,
                    ),
                ),
                "a": self.node("a", "u", self.message("m-a", "assistant", "text", ["answer"], 20.0)),
            }
            return self.conversation(cid="lineage-conflict", current="a", mapping=mapping)

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first_source = self.write_export(root, [conversation("file-a")])
            first = materialize_export(first_source, root / "first")[0]
            second_source = root / "second-export.zip"
            with zipfile.ZipFile(second_source, "w") as zf:
                zf.writestr("conversations-000.json", json.dumps([conversation("file-b")]))
            second = materialize_export(second_source, root / "second")[0]
            corpus = root / "corpus"
            self.assertEqual(ingest_artifact(first, corpus)["status"], "INGESTED")
            with self.assertRaisesRegex(RuntimeError, "FAILED_CONFLICTING_DUPLICATE"):
                ingest_artifact(second, corpus)
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

    def test_legacy_single_conversations_json_is_supported(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "chatgpt-export.zip"
            with zipfile.ZipFile(source, "w") as zf:
                zf.writestr("conversations.json", json.dumps([self.conversation(cid="legacy")]))
            artifacts = materialize_export(source, root / "out")
            self.assertEqual(len(artifacts), 2)
            self.assertEqual({normalize_artifact(path).coverage_state for path in artifacts}, {"PARTIAL_SESSION_SLICE"})

    def test_thoughts_are_preserved_as_hidden_evidence_not_dialogue(self):
        from session_search.chatgpt_export import materialize_export

        mapping = {
            "r": self.node("r", None, None),
            "u": self.node("u", "r", self.message("m-u", "user", "text", ["question"], 10.0)),
            "t": self.node("t", "u", self.message("m-t", "assistant", "thoughts", ["hidden"], 20.0)),
        }
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            artifact = normalize_artifact(
                materialize_export(
                    self.write_export(root, [self.conversation(cid="thought", current="t", mapping=mapping)]),
                    root / "out",
                )[0]
            )
            hidden = [m for m in artifact.messages if m.content_type == "thoughts"]
            self.assertEqual(len(hidden), 1)
            self.assertEqual(hidden[0].search_class, "hidden")
            self.assertEqual([m.text for m in artifact.messages if m.search_class == "dialogue"], ["question"])

    def test_missing_children_field_is_supported_but_inconsistent_hint_fails_closed(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            ok = self.write_export(root, [self.conversation(cid="no-children")])
            self.assertEqual(len(materialize_export(ok, root / "ok")), 2)

            bad = self.conversation(cid="bad-hint")
            bad["mapping"]["u"]["children"] = ["a"]
            source = self.write_export(root, [bad])
            with self.assertRaisesRegex(ValueError, "children hint inconsistent"):
                materialize_export(source, root / "bad")

    def test_broken_parent_and_nonterminal_current_fail_closed(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            broken = self.conversation(cid="broken")
            broken["mapping"]["a"]["parent"] = "missing"
            with self.assertRaisesRegex(ValueError, "broken parent"):
                materialize_export(self.write_export(root, [broken]), root / "broken-out")

            nonterminal = self.conversation(cid="nonterminal", current="u")
            with self.assertRaisesRegex(ValueError, "current_node must be terminal"):
                materialize_export(self.write_export(root, [nonterminal]), root / "nonterminal-out")

    def test_stdlib_fallback_rejects_nonstandard_nan_timestamp(self):
        from unittest import mock
        import sys
        from session_search.chatgpt_export import materialize_export

        bad = self.conversation(cid="nan-time")
        bad["mapping"]["a"]["message"]["create_time"] = float("nan")
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(root, [bad])
            with mock.patch.dict(sys.modules, {"ijson": None}):
                with self.assertRaisesRegex(ValueError, "BLOCKED_UNSUPPORTED_CHATGPT_EXPORT"):
                    materialize_export(source, root / "out")

    def test_tied_fork_timestamps_fail_closed_instead_of_using_json_order(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            tied = self.conversation(cid="tied")
            tied["mapping"]["b"]["message"]["create_time"] = 20.0
            with self.assertRaisesRegex(ValueError, "tied child timestamps"):
                materialize_export(self.write_export(root, [tied]), root / "out")

    def test_split_members_duplicate_conversation_identity_is_rejected(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            duplicate = self.conversation(cid="duplicate")
            source = self.write_export(root, [duplicate, duplicate], split=True)
            with self.assertRaisesRegex(ValueError, "duplicate conversation id"):
                materialize_export(source, root / "out")

    def test_source_path_replacement_after_snapshot_does_not_change_consumed_bytes(self):
        from unittest import mock
        import session_search.chatgpt_export as chatgpt_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(root, [self.conversation(cid="snapshot-original", title="Original snapshot")])
            original = source.read_bytes()

            replacement_path = root / "replacement.zip"
            with zipfile.ZipFile(replacement_path, "w") as zf:
                zf.writestr(
                    "conversations-000.json",
                    json.dumps([self.conversation(cid="snapshot-replacement", title="Replacement snapshot")]),
                )
            replacement = replacement_path.read_bytes()

            real_snapshot = chatgpt_export._snapshot_source
            source_replaced = False

            def racing_snapshot(source_path, staging_root):
                nonlocal source_replaced
                result = real_snapshot(source_path, staging_root)
                source.write_bytes(replacement)
                source_replaced = True
                return result

            with mock.patch("session_search.chatgpt_export._snapshot_source", side_effect=racing_snapshot):
                outputs = chatgpt_export.materialize_export(source, root / "out")

            self.assertTrue(source_replaced)
            self.assertEqual(len(outputs), 2)
            manifests = [self.payload(path)[0] for path in outputs]
            artifacts = [normalize_artifact(path) for path in outputs]
            self.assertEqual({m["source_export_sha256"] for m in manifests}, {hashlib.sha256(original).hexdigest()})
            self.assertTrue(all(artifact.session_id.startswith("snapshot-original") for artifact in artifacts))

    def test_child_artifacts_bind_parent_zip_hash_and_are_content_addressed(self):
        from session_search.chatgpt_export import materialize_export

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(root, [self.conversation(cid="hash")])
            parent_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            first = materialize_export(source, root / "out")
            first_names = [path.name for path in first]
            for path in first:
                manifest, _ = self.payload(path)
                self.assertEqual(manifest["source_export_sha256"], parent_sha)
                self.assertEqual(manifest["snapshot_scope"], "SNAPSHOT_EXPOSED_BRANCHES")
                self.assertEqual(normalize_artifact(path).coverage_state, "PARTIAL_SESSION_SLICE")

            second = materialize_export(source, root / "out")
            self.assertEqual([path.name for path in second], first_names)

            changed = self.conversation(cid="hash")
            changed["mapping"]["a"]["message"]["content"]["parts"] = ["changed A"]
            self.write_export(root, [changed])
            third = materialize_export(source, root / "changed")
            self.assertNotEqual({p.name for p in first}, {p.name for p in third})

    def test_direct_ingest_is_idempotent_and_corpus_verifies(self):
        from session_search.chatgpt_export import ingest_export
        from session_search.corpus_store import verify_corpus

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = self.write_export(root, [self.conversation(cid="ingest")])
            corpus = root / "corpus"
            first = ingest_export(source, corpus)
            self.assertEqual(first["status"], "COMPLETE")
            self.assertEqual(first["conversation_count"], 1)
            self.assertEqual(first["artifact_count"], 2)
            self.assertEqual(first["branch_count"], 2)
            self.assertEqual(first["snapshot_scope"], "SNAPSHOT_EXPOSED_BRANCHES")
            self.assertEqual({item["status"] for item in first["results"]}, {"INGESTED"})
            self.assertEqual({item["coverage_state"] for item in first["results"]}, {"PARTIAL_SESSION_SLICE"})
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")

            second = ingest_export(source, corpus)
            self.assertEqual(second["status"], "COMPLETE")
            self.assertEqual({item["status"] for item in second["results"]}, {"ALREADY_INGESTED"})
            self.assertEqual(verify_corpus(corpus)["status"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
