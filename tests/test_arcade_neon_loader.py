import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_SRC = ROOT / 'arcade' / 'session-search-materializer' / 'src'
if str(PKG_SRC) not in sys.path:
    sys.path.insert(0, str(PKG_SRC))

from session_search_arcade_materializer.neon_loader import (
    LOADER_ROLE,
    TargetMaterializationError,
    classify_existing_generation,
    load_rows_to_building,
    resolve_neon_connection_uri,
)


class ArcadeNeonLoaderTest(unittest.TestCase):
    def test_connection_uri_uses_fixed_loader_role_and_hides_api_key(self):
        class Response:
            def raise_for_status(self): return None
            def json(self): return {'uri': 'postgresql://loader:secret@example/neondb'}

        class Client:
            async def get(self, url, *, headers, params):
                self.url = url; self.headers = headers; self.params = params
                return Response()

        async def run():
            client = Client()
            uri = await resolve_neon_connection_uri(
                neon_api_key='private-neon-key',
                project_id='wild-surf-61625738',
                branch_id='br-floral-morning-b5kactgx',
                database_name='neondb',
                client=client,
            )
            self.assertTrue(uri.startswith('postgresql://'))
            self.assertEqual(client.params['role_name'], LOADER_ROLE)
            self.assertEqual(client.headers['Authorization'], 'Bearer private-neon-key')
            self.assertNotIn('private-neon-key', uri)
        asyncio.run(run())

    def test_exact_building_replay_is_idempotent(self):
        expected = {
            'generation_id': 'a' * 64,
            'shard_id': 'xai-0001',
            'provider': 'xai',
            'source_manifest_sha256': 'b' * 64,
            'projection_schema_version': 'session-search-federated-v1',
            'session_count': 60,
            'message_count': 17790,
        }
        row = ('xai-0001', 'xai', 'b' * 64, 'session-search-federated-v1', 'BUILDING', 60, 17790)
        self.assertEqual(classify_existing_generation(row, expected), 'ALREADY_BUILDING')

    def test_conflicting_existing_generation_fails_closed(self):
        expected = {
            'generation_id': 'a' * 64,
            'shard_id': 'xai-0001',
            'provider': 'xai',
            'source_manifest_sha256': 'b' * 64,
            'projection_schema_version': 'session-search-federated-v1',
            'session_count': 60,
            'message_count': 17790,
        }
        row = ('xai-9999', 'xai', 'b' * 64, 'session-search-federated-v1', 'BUILDING', 60, 17790)
        with self.assertRaisesRegex(TargetMaterializationError, 'TARGET_EXISTING_GENERATION_MISMATCH'):
            classify_existing_generation(row, expected)


    def test_fresh_load_writes_building_rows_once(self):
        manifest = {
            'generation_id': 'a' * 64,
            'shard_id': 'xai-0001',
            'provider': 'xai',
            'source_manifest_sha256': 'b' * 64,
            'projection_schema_version': 'session-search-federated-v1',
            'session_count': 2,
            'message_count': 2,
        }
        sessions = [('s1', 'One', 'COMPLETE', 1.0, 2.0), ('s2', 'Two', 'COMPLETE', None, None)]
        messages = [('s1', 0, 'm1', 'user', 'dialogue', 1.0, 'hello'), ('s2', 0, 'm2', 'assistant', 'trace', None, 'world')]

        class Cursor:
            def __init__(self):
                self.executed = []
                self.fetch = None
                self.generation_inserted = False
                self.session_rows = 0
                self.message_rows = 0
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def execute(self, sql, params=None):
                self.executed.append((sql, params))
                normalized = ' '.join(sql.split())
                if normalized.startswith('SELECT g.shard_id'):
                    if not self.generation_inserted:
                        self.fetch = None
                    else:
                        self.fetch = ('xai-0001','xai','b' * 64,'session-search-federated-v1','BUILDING',self.session_rows,self.message_rows)
                elif normalized.startswith('INSERT INTO session_search.generations'):
                    self.generation_inserted = True
                elif normalized.startswith('SELECT state,'):
                    self.fetch = ('BUILDING', self.session_rows, self.message_rows)
            def fetchone(self): return self.fetch

        class Connection:
            def __init__(self): self.cursor_obj = Cursor(); self.commits = 0; self.rollbacks = 0
            def __enter__(self): return self
            def __exit__(self, exc_type, *_args):
                if exc_type is None: self.commits += 1
                else: self.rollbacks += 1
                return False
            def cursor(self): return self.cursor_obj
            def close(self): pass

        conn = Connection()
        def connect(_url): return conn
        def execute_values(cur, sql, rows, page_size):
            rows = list(rows)
            if 'session_search.sessions' in sql:
                cur.session_rows += len(rows)
            elif 'session_search.messages' in sql:
                cur.message_rows += len(rows)
            else:
                raise AssertionError(sql)

        result = load_rows_to_building(
            manifest=manifest, sessions=sessions, messages=messages,
            database_url='postgresql://private', connect=connect,
            execute_values_fn=execute_values, batch_size=500,
        )
        self.assertEqual(result['status'], 'LOADED')
        self.assertEqual(result['target_state'], 'BUILDING')
        self.assertEqual(result['loaded_sessions'], 2)
        self.assertEqual(result['loaded_messages'], 2)
        self.assertEqual(conn.commits, 1)

    def test_exact_building_db_replay_performs_no_insert(self):
        manifest = {
            'generation_id': 'a' * 64, 'shard_id': 'xai-0001', 'provider': 'xai',
            'source_manifest_sha256': 'b' * 64, 'projection_schema_version': 'session-search-federated-v1',
            'session_count': 60, 'message_count': 17790,
        }
        existing = ('xai-0001','xai','b' * 64,'session-search-federated-v1','BUILDING',60,17790)
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def execute(self, sql, params=None): self.sql = sql
            def fetchone(self): return existing
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def cursor(self): return Cursor()
            def close(self): pass
        def forbidden_execute_values(*_args, **_kwargs): raise AssertionError('replay must not insert')
        result = load_rows_to_building(
            manifest=manifest, sessions=[], messages=[], database_url='postgresql://private',
            connect=lambda _url: Connection(), execute_values_fn=forbidden_execute_values,
        )
        self.assertEqual(result['status'], 'ALREADY_BUILDING')
        self.assertEqual(result['loaded_sessions'], 60)
        self.assertEqual(result['loaded_messages'], 17790)

    def test_server_exposes_building_only_materializer(self):
        server = (PKG_SRC / 'session_search_arcade_materializer' / 'server.py').read_text()
        self.assertIn('materialize_bundle', server)
        self.assertIn('NEON_API_KEY', server)
        self.assertIn('LOADER_ROLE', server)
        loader = (PKG_SRC / 'session_search_arcade_materializer' / 'neon_loader.py').read_text()
        self.assertIn("LOADER_ROLE = 'session_search_arcade_loader_sql'", loader)
        self.assertNotIn('role_name: Annotated', server)
        self.assertIn('read_verified_bundle', server)
        self.assertIn('load_rows_to_building', server)
        self.assertNotIn('VERIFIED_NOT_LOADED', server)
        self.assertNotIn('mark_generation_ready', server)
        self.assertNotIn('activate_generation', server)


if __name__ == '__main__':
    unittest.main()
