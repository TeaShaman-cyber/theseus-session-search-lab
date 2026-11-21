import hashlib
import io
import json
import pathlib
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock

from session_search.artifact import normalize_artifact


GEMINI_MEMBER = "Takeout/Мои действия/Приложения Gemini/МоиДействия.html"
AI_MODE_MEMBER = "Takeout/Мои действия/Режим ИИ/МоиДействия.html"


def activity_card(
    *,
    user: str,
    answer: str,
    timestamp: str,
    app_ids: list[str],
    attachment_hrefs: list[str] | None = None,
) -> str:
    links = "".join(
        f'<a href="https://gemini.google.com/app/{app_id}">'
        f'https://gemini.google.com/app/{app_id}</a>'
        for app_id in app_ids
    )
    attachments = "".join(
        f'<a href="{href}">{href}</a>' for href in (attachment_hrefs or [])
    )
    return (
        '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">'
        '<div class="header-cell mdl-cell mdl-cell--12-col">'
        '<div class="mdl-typography--title">Приложения Gemini</div></div>'
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
        f'Отправлен запрос {user}{attachments}<br>{timestamp}<br>{answer}'
        '</div>'
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1 '
        'mdl-typography--text-right"></div>'
        '<div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">'
        f'Продукты: Приложения Gemini Подробная информация: {links}'
        '</div>'
        '</div>'
    )


def feedback_card(timestamp: str) -> str:
    return (
        '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">'
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
        f'Ваш отзыв: Хороший ответ<br>{timestamp}'
        '</div>'
        '<div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">'
        'Продукты: Приложения Gemini'
        '</div></div>'
    )


def write_takeout(
    path: pathlib.Path,
    *,
    gemini_html: str,
    extra_members: dict[str, bytes] | None = None,
    ai_mode_html: str | None = None,
    gemini_member: str = GEMINI_MEMBER,
) -> pathlib.Path:
    members: dict[str, bytes] = {gemini_member: gemini_html.encode("utf-8")}
    if ai_mode_html is not None:
        members[AI_MODE_MEMBER] = ai_mode_html.encode("utf-8")
    members.update(extra_members or {})
    with tarfile.open(path, "w:gz") as tf:
        for name in sorted(members):
            data = members[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    return path


class GoogleGeminiMyActivityAdapterTest(unittest.TestCase):
    def test_single_app_cards_materialize_partial_conversation_in_chronological_order(self):
        from session_search.google_gemini_activity import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            newer = activity_card(
                user="new question",
                answer="new answer",
                timestamp="21 нояб. 2026 г., 16:28:40 CET",
                app_ids=["abc123"],
            )
            older = activity_card(
                user="old question",
                answer="old answer",
                timestamp="20 нояб. 2026 г., 10:00:00 CET",
                app_ids=["abc123"],
            )
            source = write_takeout(root / "takeout.tgz", gemini_html=newer + older)
            outputs = materialize_takeout(source, root / "out")
            self.assertEqual(len(outputs), 1)
            artifact = normalize_artifact(outputs[0])
            self.assertEqual(artifact.source_adapter, "google-gemini-myactivity")
            self.assertEqual(artifact.session_id, "gemini:abc123")
            self.assertEqual(artifact.source_conversation_id, "abc123")
            self.assertEqual(artifact.coverage_state, "PARTIAL_SESSION_SLICE")
            self.assertEqual(
                [(m.role, m.text) for m in artifact.messages],
                [
                    ("user", "old question"),
                    ("assistant", "old answer"),
                    ("user", "new question"),
                    ("assistant", "new answer"),
                ],
            )
            self.assertIsNotNone(artifact.messages[0].create_time)
            self.assertIsNone(artifact.messages[1].create_time)
            orders = [m.provider_order for m in artifact.messages]
            self.assertTrue(all(isinstance(value, int) for value in orders))
            self.assertEqual(orders, sorted(orders))
            self.assertEqual(orders[1], orders[0] + 1)
            self.assertEqual(orders[3], orders[2] + 1)

    def test_multi_app_prompt_is_preserved_as_unresolved_instead_of_misattributed(self):
        from session_search.google_gemini_activity import inspect_takeout, materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=activity_card(
                    user="ambiguous question",
                    answer="ambiguous answer",
                    timestamp="21 нояб. 2026 г., 16:28:40 CET",
                    app_ids=["a", "b"],
                ),
            )
            outputs = materialize_takeout(source, root / "out")
            self.assertEqual(len(outputs), 1)
            artifact = normalize_artifact(outputs[0])
            self.assertTrue(artifact.session_id.startswith("gemini-unresolved:"))
            self.assertIsNone(artifact.source_conversation_id)
            self.assertEqual(
                [(m.role, m.text) for m in artifact.messages],
                [("user", "ambiguous question"), ("assistant", "ambiguous answer")],
            )
            receipt = inspect_takeout(source)
            self.assertEqual(receipt["ambiguous_multi_app_prompt_cards"], 1)
            self.assertEqual(receipt["resolved_prompt_cards"], 0)
            self.assertEqual(receipt["unresolved_artifact_count"], 1)

    def test_empty_assistant_response_preserves_user_turn_without_inventing_response(self):
        from session_search.google_gemini_activity import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=activity_card(
                    user="question only",
                    answer="",
                    timestamp="21 нояб. 2026 г., 16:28:40 CET",
                    app_ids=["abc123"],
                ),
            )
            artifact = normalize_artifact(materialize_takeout(source, root / "out")[0])
            self.assertEqual([(m.role, m.text) for m in artifact.messages], [("user", "question only")])

    def test_attachment_refs_record_resolved_hash_and_unresolved_reference(self):
        from session_search.google_gemini_activity import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            resolved = (
                "Takeout/Мои действия/Приложения Gemini/"
                "note file-abc.txt"
            )
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=activity_card(
                    user="see attachments",
                    answer="reviewed",
                    timestamp="21 нояб. 2026 г., 16:28:40 CET",
                    app_ids=["abc123"],
                    attachment_hrefs=["note%20file-abc.txt", "missing%20file.pdf"],
                ),
                extra_members={resolved: b"attachment bytes"},
            )
            child = materialize_takeout(source, root / "out")[0]
            with zipfile.ZipFile(child) as zf:
                payload_name = next(n for n in zf.namelist() if n.startswith("optional/conversation-"))
                payload = json.loads(zf.read(payload_name))
            metadata = payload["messages"][0]["metadata"]
            refs = metadata["gemini_attachment_refs"]
            self.assertEqual(len(refs), 2)
            self.assertEqual(refs[0]["resolution"], "RESOLVED")
            self.assertEqual(refs[0]["bytes"], len(b"attachment bytes"))
            self.assertEqual(refs[0]["sha256"], hashlib.sha256(b"attachment bytes").hexdigest())
            self.assertEqual(refs[1]["resolution"], "UNRESOLVED_ATTACHMENT_REF")
            self.assertNotIn("sha256", refs[1])

    def test_ai_mode_activity_is_not_imported_as_gemini_chat(self):
        from session_search.google_gemini_activity import inspect_takeout, materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=feedback_card("21 нояб. 2026 г., 16:21:25 CET"),
                ai_mode_html=activity_card(
                    user="AI mode question",
                    answer="AI mode answer",
                    timestamp="21 нояб. 2026 г., 16:28:40 CET",
                    app_ids=["should-not-import"],
                ),
            )
            self.assertEqual(materialize_takeout(source, root / "out"), [])
            receipt = inspect_takeout(source)
            self.assertEqual(receipt["gemini_activity_cards"], 1)
            self.assertEqual(receipt["prompt_cards"], 0)
            self.assertEqual(receipt["ignored_ai_mode_members"], 1)

    def test_materialization_is_deterministic_and_binds_parent_and_member_hashes(self):
        from session_search.google_gemini_activity import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=activity_card(
                    user="deterministic",
                    answer="answer",
                    timestamp="21 нояб. 2026 г., 16:28:40 CET",
                    app_ids=["abc123"],
                ),
            )
            parent_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            first = materialize_takeout(source, root / "one")
            second = materialize_takeout(source, root / "two")
            self.assertEqual([p.read_bytes() for p in first], [p.read_bytes() for p in second])
            with zipfile.ZipFile(first[0]) as zf:
                manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["source_export_sha256"], parent_sha)
            self.assertEqual(manifest["source_member"], GEMINI_MEMBER)
            self.assertRegex(manifest["source_member_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(manifest["snapshot_scope"], "OFFICIAL_MY_ACTIVITY_EXPOSED_SNAPSHOT")

    def test_parent_hash_and_parse_use_one_captured_archive_snapshot(self):
        import session_search.google_gemini_activity as adapter

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=activity_card(
                    user="old question",
                    answer="old answer",
                    timestamp="21 нояб. 2026 г., 16:28:40 CET",
                    app_ids=["old"],
                ),
            )
            replacement = write_takeout(
                root / "replacement.tgz",
                gemini_html=activity_card(
                    user="replacement question",
                    answer="replacement answer",
                    timestamp="21 нояб. 2026 г., 16:28:40 CET",
                    app_ids=["new"],
                ),
            ).read_bytes()
            original_parent = source.read_bytes()
            real_read_bytes = pathlib.Path.read_bytes
            mutated = {"done": False}

            def read_then_replace(path):
                data = real_read_bytes(path)
                if path == source and not mutated["done"]:
                    source.write_bytes(replacement)
                    mutated["done"] = True
                return data

            with mock.patch.object(pathlib.Path, "read_bytes", new=read_then_replace):
                child = adapter.materialize_takeout(source, root / "out")[0]
            artifact = normalize_artifact(child)
            self.assertEqual(artifact.session_id, "gemini:old")
            self.assertEqual([m.text for m in artifact.messages], ["old question", "old answer"])
            with zipfile.ZipFile(child) as zf:
                manifest = json.loads(zf.read("manifest.json"))
            self.assertEqual(manifest["source_export_sha256"], hashlib.sha256(original_parent).hexdigest())

    def test_inspect_receipt_counts_prompt_feedback_and_unresolved_cards(self):
        from session_search.google_gemini_activity import inspect_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=(
                    activity_card(
                        user="one",
                        answer="answer",
                        timestamp="21 нояб. 2026 г., 16:28:40 CET",
                        app_ids=["abc"],
                    )
                    + activity_card(
                        user="ambiguous",
                        answer="answer",
                        timestamp="21 нояб. 2026 г., 16:27:40 CET",
                        app_ids=["a", "b"],
                    )
                    + feedback_card("21 нояб. 2026 г., 16:21:25 CET")
                ),
            )
            first = inspect_takeout(source)
            second = inspect_takeout(source)
            self.assertEqual(first, second)
            self.assertEqual(first["gemini_activity_cards"], 3)
            self.assertEqual(first["prompt_cards"], 2)
            self.assertEqual(first["resolved_prompt_cards"], 1)
            self.assertEqual(first["ambiguous_multi_app_prompt_cards"], 1)
            self.assertEqual(first["other_activity_cards"], 1)
            self.assertEqual(first["resolved_conversation_count"], 1)
            self.assertEqual(first["unresolved_artifact_count"], 1)
            self.assertEqual(first["message_count"], 4)

    def test_quoted_timestamp_inside_response_does_not_hide_provider_timestamp(self):
        from session_search.google_gemini_activity import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=activity_card(
                    user="what time did we mention",
                    answer=(
                        "We previously wrote 20 нояб. 2026 г., 12:00:00 CET "
                        "inside the conversation."
                    ),
                    timestamp="21 нояб. 2026 г., 16:28:40 CET",
                    app_ids=["abc123"],
                ),
            )
            artifact = normalize_artifact(materialize_takeout(source, root / "out")[0])
            self.assertEqual(
                [(m.role, m.text) for m in artifact.messages],
                [
                    ("user", "what time did we mention"),
                    (
                        "assistant",
                        "We previously wrote 20 нояб. 2026 г., 12:00:00 CET inside the conversation.",
                    ),
                ],
            )

    def test_same_second_identical_assistant_text_keeps_distinct_activity_occurrences(self):
        from session_search.google_gemini_activity import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            timestamp = "21 нояб. 2026 г., 16:28:40 CET"
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=(
                    activity_card(
                        user="second question",
                        answer="OK",
                        timestamp=timestamp,
                        app_ids=["abc123"],
                    )
                    + activity_card(
                        user="first question",
                        answer="OK",
                        timestamp=timestamp,
                        app_ids=["abc123"],
                    )
                ),
            )
            artifact = normalize_artifact(materialize_takeout(source, root / "out")[0])
            self.assertEqual(len(artifact.messages), 4)
            assistants = [m for m in artifact.messages if m.role == "assistant"]
            self.assertEqual([m.text for m in assistants], ["OK", "OK"])
            self.assertEqual(len({m.message_id for m in assistants}), 2)

    def test_overlapping_partial_snapshots_keep_prompt_response_pairs_in_provider_order(self):
        from session_search.google_gemini_activity import ingest_takeout
        import sqlite3

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            corpus = root / "corpus"

            a = activity_card(
                user="A question",
                answer="A answer",
                timestamp="20 нояб. 2026 г., 10:00:00 CET",
                app_ids=["abc123"],
            )
            b = activity_card(
                user="B question",
                answer="B answer",
                timestamp="20 нояб. 2026 г., 11:00:00 CET",
                app_ids=["abc123"],
            )
            c = activity_card(
                user="C question",
                answer="C answer",
                timestamp="20 нояб. 2026 г., 12:00:00 CET",
                app_ids=["abc123"],
            )

            first = write_takeout(root / "first.tgz", gemini_html=b + a)
            second = write_takeout(root / "second.tgz", gemini_html=c + b)

            self.assertEqual(ingest_takeout(first, corpus)["status"], "COMPLETE")
            self.assertEqual(ingest_takeout(second, corpus)["status"], "COMPLETE")

            con = sqlite3.connect(corpus / "corpus.sqlite3")
            rows = con.execute(
                "SELECT role,text,provider_order FROM messages "
                "WHERE session_id=? ORDER BY ordinal",
                ("gemini:abc123",),
            ).fetchall()
            con.close()

            self.assertEqual(
                [(role, text) for role, text, _ in rows],
                [
                    ("user", "A question"),
                    ("assistant", "A answer"),
                    ("user", "B question"),
                    ("assistant", "B answer"),
                    ("user", "C question"),
                    ("assistant", "C answer"),
                ],
            )
            orders = [order for _, _, order in rows]
            self.assertEqual(orders, sorted(orders))

    def test_english_myactivity_member_fails_closed_until_locale_is_supported(self):
        from session_search.google_gemini_activity import inspect_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=(
                    '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">'
                    '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
                    'Prompted hello<br>Nov 21, 2026, 4:28:40 PM CET<br>answer'
                    '</div>'
                    '<div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">'
                    '<a href="https://gemini.google.com/app/abc123">chat</a>'
                    '</div></div>'
                ),
                gemini_member="Takeout/My Activity/Gemini Apps/MyActivity.html",
            )
            with self.assertRaisesRegex(
                ValueError, "BLOCKED_UNSUPPORTED_GEMINI_ACTIVITY_LOCALE"
            ):
                inspect_takeout(source)

    def test_response_preserves_table_boundaries_and_preformatted_whitespace(self):
        from session_search.google_gemini_activity import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=activity_card(
                    user="show structured answer",
                    answer=(
                        "<table><tr><th>Setting</th><th>Value</th></tr>"
                        "<tr><td>timeout</td><td>30</td></tr></table>"
                        "<pre><code>if ready:\n    run()\n\nelse:\n    stop()</code></pre>"
                    ),
                    timestamp="21 нояб. 2026 г., 16:28:40 CET",
                    app_ids=["abc123"],
                ),
            )
            artifact = normalize_artifact(materialize_takeout(source, root / "out")[0])
            answer = artifact.messages[1].text
            self.assertNotIn("SettingValue", answer)
            self.assertNotIn("timeout30", answer)
            self.assertIn("Setting", answer)
            self.assertIn("Value", answer)
            self.assertIn("if ready:\n    run()\n\nelse:\n    stop()", answer)

    def test_reconciliation_reports_exact_overlap_without_routing_or_deleting_rescue(self):
        from session_search.google_gemini_activity import reconcile_notebooklm_rescue
        from session_search.notebooklm_gemini import ingest_takeout as ingest_notebooklm

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            notebook = root / "notebook.zip"
            with zipfile.ZipFile(notebook, "w") as zf:
                zf.writestr(
                    "Takeout/NotebookLM/Test/Sources/Conversation.html",
                    "<p><strong>User</strong> : shared question"
                    "<strong>Gemini</strong> : shared answer</p>",
                )
            corpus = root / "corpus"
            ingest_notebooklm(notebook, corpus)

            official = write_takeout(
                root / "takeout.tgz",
                gemini_html=activity_card(
                    user="shared question",
                    answer="shared answer",
                    timestamp="21 нояб. 2026 г., 16:28:40 CET",
                    app_ids=["abc123"],
                ),
            )
            report = reconcile_notebooklm_rescue(official, corpus)
            self.assertEqual(report["rescue_artifacts"], 1)
            self.assertEqual(report["rescue_message_occurrences"], 2)
            self.assertEqual(report["official_artifacts"], 1)
            self.assertEqual(report["official_message_occurrences"], 2)
            self.assertEqual(report["rescue_sessions_unique_exact_best_match"], 1)
            self.assertEqual(report["rescue_sessions_zero_exact_overlap"], 0)
            self.assertEqual(report["rescue_message_occurrences_with_any_official_exact_match"], 2)
            self.assertEqual(report["routing_action"], "NONE_EVIDENCE_PRESERVED")

    def test_ingest_reuses_existing_corpus_search(self):
        from session_search.google_gemini_activity import ingest_takeout
        from session_search.search import search_corpus

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = write_takeout(
                root / "takeout.tgz",
                gemini_html=activity_card(
                    user="official invariant bridge",
                    answer="official preserved route",
                    timestamp="21 нояб. 2026 г., 16:28:40 CET",
                    app_ids=["abc123"],
                ),
            )
            corpus = root / "corpus"
            result = ingest_takeout(source, corpus)
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(result["resolved_conversation_count"], 1)
            self.assertEqual(result["message_count"], 2)
            hits = search_corpus(corpus, "official invariant bridge", ["dialogue"], 8)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["representative"]["session_id"], "gemini:abc123")
            self.assertEqual(hits[0]["representative"]["session_coverage"], "PARTIAL_SESSION_SLICE")


if __name__ == "__main__":
    unittest.main()
