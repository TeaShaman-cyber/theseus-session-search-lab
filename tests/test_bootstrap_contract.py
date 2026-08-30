import json
import pathlib
import subprocess
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def synthetic_export(path: pathlib.Path) -> None:
    conversation = {
        "title": "Synthetic continuity test",
        "conversation_id": "synthetic-conversation",
        "current_node": "m4",
        "page_info": {"has_previous_page": True, "has_next_page": False},
        "messages": [
            {"id":"m1","author":{"role":"user"},"create_time":1.0,"content":{"content_type":"text","parts":["remember the copper kettle decision"]}},
            {"id":"m2","author":{"role":"assistant"},"create_time":2.0,"content":{"content_type":"text","parts":["The copper kettle is the selected option."]}},
            {"id":"m3","author":{"role":"tool"},"create_time":3.0,"content":{"content_type":"code","text":"remote SHA verified"}},
            {"id":"m4","author":{"role":"assistant"},"create_time":4.0,"content":{"content_type":"thoughts","thoughts":[{"summary":"hidden internal trace"}]}}
        ]
    }
    payload = json.dumps(conversation, ensure_ascii=False).encode()
    manifest = {
        "schema":"barn-doctor-export:v1",
        "files":[{"name":"optional/conversation-1.bin","bytes":len(payload),"sha256":__import__('hashlib').sha256(payload).hexdigest()}]
    }
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        z.writestr('optional/conversation-1.bin', payload)


def synthetic_multipage_export(path: pathlib.Path, *, conflict_duplicate: bool = False) -> None:
    import hashlib

    detail = {
        "title": "Synthetic complete conversation",
        "conversation_id": "synthetic-complete",
        "current_node": "m4",
        "page_info": {
            "start_cursor": "m3",
            "end_cursor": "m4",
            "has_previous_page": True,
            "has_next_page": False,
        },
        "messages": [
            {"id":"m3","author":{"role":"user"},"create_time":3.0,"content":{"content_type":"text","parts":["newer question about the brass compass"]}},
            {"id":"m4","author":{"role":"assistant"},"create_time":4.0,"content":{"content_type":"text","parts":["The brass compass remains selected."]}},
        ],
    }
    middle = {
        "messages": [
            {"id":"m2","author":{"role":"assistant"},"create_time":2.0,"content":{"content_type":"text","parts":["The copper kettle is the selected option."]}},
        ],
        "page_info": {
            "start_cursor": "m2",
            "end_cursor": "m2",
            "has_previous_page": True,
            "has_next_page": True,
        },
    }
    oldest_m2_text = "CONFLICTING duplicate" if conflict_duplicate else "The copper kettle is the selected option."
    oldest = {
        "messages": [
            {"id":"m1","author":{"role":"user"},"create_time":1.0,"content":{"content_type":"text","parts":["oldest visible decision"]}},
            {"id":"m2","author":{"role":"assistant"},"create_time":2.0,"content":{"content_type":"text","parts":[oldest_m2_text]}},
        ],
        "page_info": {
            "start_cursor": "m1",
            "end_cursor": "m2",
            "has_previous_page": False,
            "has_next_page": True,
        },
    }
    members = [
        ("optional/conversation-100.bin", detail),
        ("optional/conversation-messages-200.bin", middle),
        ("optional/conversation-messages-300.bin", oldest),
    ]
    encoded = [(name, json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()) for name, obj in members]
    manifest = {
        "schema":"barn-doctor-export:v1",
        "files":[
            {"name":name,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
            for name, data in encoded
        ],
    }
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        for name, data in encoded:
            z.writestr(name, data)


class BootstrapContract(unittest.TestCase):
    def test_readme_declares_portable_runtime_and_verified_wiki_boundary(self):
        text = (ROOT / 'README.md').read_text() if (ROOT / 'README.md').exists() else ''
        self.assertIn('MarcoPolo is not a runtime dependency', text)
        self.assertIn('Wiki: WIKI_GIT_REMOTE_VERIFIED', text)
        self.assertIn('fab484e1e22c982229d2aab2d80c933f4c5c1d93', text)
        self.assertTrue((ROOT / 'receipts/001-wiki-bootstrap.json').is_file())
        self.assertIn('Session history is evidence, not semantic memory', text)

    def test_portable_import_and_search_work_without_marcopolo(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            src = td / 'capture.zip'
            db = td / 'session.sqlite3'
            synthetic_export(src)
            imp = subprocess.run(
                ['python3','-m','session_search.importer',str(src),'--db',str(db)],
                cwd=ROOT, text=True, capture_output=True
            )
            self.assertEqual(imp.returncode, 0, imp.stderr)
            result = subprocess.run(
                ['python3','-m','session_search.search','copper kettle','--db',str(db),'--json'],
                cwd=ROOT, text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = json.loads(result.stdout)
            self.assertGreaterEqual(len(rows), 1)
            self.assertTrue(any('copper kettle' in r['text'].lower() for r in rows))
            self.assertTrue(all(r['content_type'] != 'thoughts' for r in rows))

    def test_partial_capture_is_preserved_as_coverage_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            src = td / 'capture.zip'
            db = td / 'session.sqlite3'
            synthetic_export(src)
            subprocess.run(['python3','-m','session_search.importer',str(src),'--db',str(db)], cwd=ROOT, check=False)
            q = subprocess.run(
                ['python3','-c',
                 'import sqlite3,sys,json; c=sqlite3.connect(sys.argv[1]); print(json.dumps(c.execute("select coverage_state,has_previous_page from sessions").fetchone()))',
                 str(db)], cwd=ROOT, text=True, capture_output=True
            )
            self.assertEqual(q.returncode, 0, q.stderr)
            self.assertEqual(json.loads(q.stdout), ['PARTIAL_SESSION_SLICE', 1])


    def test_multipage_capture_merges_to_complete_exposed_conversation_with_page_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            src = td / 'capture.zip'
            db = td / 'session.sqlite3'
            synthetic_multipage_export(src)
            imp = subprocess.run(
                ['python3','-m','session_search.importer',str(src),'--db',str(db)],
                cwd=ROOT, text=True, capture_output=True
            )
            self.assertEqual(imp.returncode, 0, imp.stderr)
            summary = json.loads(imp.stdout)
            self.assertEqual(summary['coverage_state'], 'COMPLETE_EXPOSED_CONVERSATION')
            self.assertEqual(summary['messages'], 4)
            self.assertEqual(summary['payload_pages'], 3)
            self.assertEqual(summary['duplicate_message_occurrences'], 1)

            q = subprocess.run(
                ['python3','-c',
                 'import sqlite3,sys,json; c=sqlite3.connect(sys.argv[1]); '
                 'print(json.dumps({"session":c.execute("select coverage_state,has_previous_page,has_next_page from sessions").fetchone(),'
                 '"ids":[r[0] for r in c.execute("select message_id from messages order by ordinal")],'
                 '"pages":c.execute("select count(*) from payload_pages").fetchone()[0],'
                 '"sources":c.execute("select count(*) from message_sources where message_id=\'m2\'").fetchone()[0]}))',
                 str(db)], cwd=ROOT, text=True, capture_output=True
            )
            self.assertEqual(q.returncode, 0, q.stderr)
            state = json.loads(q.stdout)
            self.assertEqual(state['session'], ['COMPLETE_EXPOSED_CONVERSATION', 0, 0])
            self.assertEqual(state['ids'], ['m1','m2','m3','m4'])
            self.assertEqual(state['pages'], 3)
            self.assertEqual(state['sources'], 2)

    def test_conflicting_duplicate_message_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            src = td / 'capture.zip'
            db = td / 'session.sqlite3'
            synthetic_multipage_export(src, conflict_duplicate=True)
            imp = subprocess.run(
                ['python3','-m','session_search.importer',str(src),'--db',str(db)],
                cwd=ROOT, text=True, capture_output=True
            )
            self.assertNotEqual(imp.returncode, 0)
            self.assertIn('conflicting duplicate message_id', imp.stderr)


if __name__ == '__main__':
    unittest.main()
