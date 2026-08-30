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


if __name__ == '__main__':
    unittest.main()
