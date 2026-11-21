import datetime
import hashlib
import io
import json
import pathlib
import tarfile
import tempfile
import unittest
import zipfile

from session_search.artifact import normalize_artifact


GEMINI_MEMBER = "Takeout/Мои действия/Приложения Gemini/МоиДействия.html"


def card(main_html: str, app_ids=(), extra_left_html: str = "") -> str:
    links = "".join(
        f'<a href="https://gemini.google.com/app/{app_id}">'
        f'https://gemini.google.com/app/{app_id}</a>'
        for app_id in app_ids
    )
    return (
        '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">'
        '<div class="header-cell mdl-cell mdl-cell--12-col">'
        '<div class="mdl-typography--title">Приложения Gemini</div></div>'
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
        f'{main_html}{extra_left_html}</div>'
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1 mdl-typography--text-right"></div>'
        '<div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">'
        'Продукты:<br>Приложения Gemini<br>Подробная информация:<br>'
        f'{links}'
        '</div></div>'
    )


def request(prompt: str, response: str, timestamp: str, app_ids=(), attachment_href: str | None = None) -> str:
    attachment = ""
    if attachment_href:
        attachment = f'<br>Прикреплен 1 файл<br><a href="{attachment_href}">{attachment_href}</a>'
    main = f'Отправлен запрос {prompt}{attachment}<br>{timestamp}'
    if response:
        main += f'<br>{response}'
    return card(main, app_ids)


def feedback(text: str, timestamp: str) -> str:
    return card(f'Ваш отзыв: {text}<br>{timestamp}')


def make_takeout(path: pathlib.Path, activity_html: str, extras: dict[str, bytes] | None = None) -> pathlib.Path:
    extras = extras or {}
    with tarfile.open(path, "w:gz") as tf:
        payload = activity_html.encode("utf-8")
        info = tarfile.TarInfo(GEMINI_MEMBER)
        info.size = len(payload)
        info.mtime = 0
        tf.addfile(info, io.BytesIO(payload))
        for name, data in sorted(extras.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(data))
    return path


class GoogleMyActivityGeminiTest(unittest.TestCase):
    def test_single_id_requests_group_into_provider_session_and_restore_chronology(self):
        from session_search.google_my_activity_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            html = (
                request("new q", "new a", "21 нояб. 2026 г., 16:28:40 CET", ["abc123"])
                + feedback("Хороший ответ", "21 нояб. 2026 г., 16:27:00 CET")
                + request("old q", "old a", "21 окт. 2026 г., 11:56:23 CET", ["abc123"])
            )
            source = make_takeout(root / "takeout.tgz", html)
            outputs = materialize_takeout(source, root / "out")
            self.assertEqual(len(outputs), 1)
            artifact = normalize_artifact(outputs[0])
            self.assertEqual(artifact.session_id, "google-gemini:abc123")
            self.assertEqual(artifact.source_adapter, "google-my-activity-gemini")
            self.assertEqual(artifact.coverage_state, "PARTIAL_SESSION_SLICE")
            self.assertEqual(
                [(m.role, m.text) for m in artifact.messages],
                [
                    ("user", "old q"),
                    ("assistant", "old a"),
                    ("user", "new q"),
                    ("assistant", "new a"),
                ],
            )
            self.assertEqual(
                artifact.messages[0].create_time,
                datetime.datetime(2026, 10, 21, 11, 56, 23, tzinfo=datetime.timezone(datetime.timedelta(hours=1))).timestamp(),
            )

    def test_prompt_only_request_is_preserved_without_inventing_assistant_reply(self):
        from session_search.google_my_activity_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = make_takeout(
                root / "takeout.tgz",
                request("only q", "", "21 нояб. 2026 г., 16:28:40 CET", ["solo"]),
            )
            artifact = normalize_artifact(materialize_takeout(source, root / "out")[0])
            self.assertEqual([(m.role, m.text) for m in artifact.messages], [("user", "only q")])

    def test_multi_id_request_becomes_separate_event_instead_of_being_duplicated(self):
        from session_search.google_my_activity_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            html = (
                request("normal", "answer", "21 нояб. 2026 г., 16:28:40 CET", ["one"])
                + request("cross", "cross answer", "21 нояб. 2026 г., 16:22:17 CET", ["one", "two"])
            )
            source = make_takeout(root / "takeout.tgz", html)
            artifacts = [normalize_artifact(p) for p in materialize_takeout(source, root / "out")]
            self.assertEqual(len(artifacts), 2)
            sessions = {a.session_id: a for a in artifacts}
            self.assertIn("google-gemini:one", sessions)
            multi = [a for a in artifacts if a.session_id.startswith("google-gemini-multi:")]
            self.assertEqual(len(multi), 1)
            self.assertEqual([m.text for m in sessions["google-gemini:one"].messages], ["normal", "answer"])
            self.assertEqual([m.text for m in multi[0].messages], ["cross", "cross answer"])

    def test_attachment_reference_is_bound_to_takeout_member_hash(self):
        from session_search.google_my_activity_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            attachment = b"attachment bytes"
            member = "Takeout/Мои действия/Приложения Gemini/photo.png"
            source = make_takeout(
                root / "takeout.tgz",
                request(
                    "with file",
                    "seen",
                    "21 нояб. 2026 г., 16:28:40 CET",
                    ["attach"],
                    "photo.png",
                ),
                {member: attachment},
            )
            child = materialize_takeout(source, root / "out")[0]
            with zipfile.ZipFile(child) as zf:
                payload_name = [n for n in zf.namelist() if n != "manifest.json"][0]
                payload = json.loads(zf.read(payload_name))
            refs = payload["google_my_activity_attachment_refs"]
            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0]["href"], "photo.png")
            self.assertEqual(refs[0]["member"], member)
            self.assertEqual(refs[0]["bytes"], len(attachment))
            self.assertEqual(refs[0]["sha256"], hashlib.sha256(attachment).hexdigest())

    def test_cest_timestamp_is_supported(self):
        from session_search.google_my_activity_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = make_takeout(
                root / "takeout.tgz",
                request("summer", "answer", "9 июл. 2026 г., 18:47:53 CEST", ["summer"]),
            )
            artifact = normalize_artifact(materialize_takeout(source, root / "out")[0])
            self.assertEqual(
                artifact.messages[0].create_time,
                datetime.datetime(2026, 7, 9, 18, 47, 53, tzinfo=datetime.timezone(datetime.timedelta(hours=2))).timestamp(),
            )

    def test_malformed_request_timestamp_blocks_instead_of_silently_skipping(self):
        from session_search.google_my_activity_gemini import materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = make_takeout(
                root / "takeout.tgz",
                card("Отправлен запрос q<br>timestamp changed<br>a", ["bad"]),
            )
            with self.assertRaisesRegex(ValueError, "BLOCKED_UNSUPPORTED_GOOGLE_MY_ACTIVITY_GEMINI"):
                materialize_takeout(source, root / "out")

    def test_materialization_and_inspection_are_deterministic(self):
        from session_search.google_my_activity_gemini import inspect_takeout, materialize_takeout

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = make_takeout(
                root / "takeout.tgz",
                request("q", "a", "21 нояб. 2026 г., 16:28:40 CET", ["det"]),
            )
            first = materialize_takeout(source, root / "one")
            second = materialize_takeout(source, root / "two")
            self.assertEqual([p.read_bytes() for p in first], [p.read_bytes() for p in second])
            self.assertEqual(inspect_takeout(source), inspect_takeout(source))


if __name__ == "__main__":
    unittest.main()
