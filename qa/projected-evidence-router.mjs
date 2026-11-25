// Test-only transport bridge: real router and shard handlers, disposable PG.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { makeRouterHandler } from '../functions/router/handler.mjs';
import { makeShardHandler } from '../functions/shard/handler.mjs';

assert.equal(process.env.PGDATABASE, 'ss_projection_contract');
const input = JSON.parse(readFileSync(0, 'utf8'));
const shard = makeShardHandler(async (call) => {
  assert.equal(call.text, 'SELECT session_search.read_generation_session_v1($1,$2,$3,$4,$5,$6,$7) AS result');
  const [generation, session, ordinal, mid, offset, limit, chars] = call.values;
  const args = ['-X', '-qAt', '-v', 'ON_ERROR_STOP=1'];
  for (const [key, value] of Object.entries({generation, session, ordinal, mid: mid ?? '', offset, limit, chars})) {
    args.push('-v', key + '=' + String(value));
  }
  const midSql = mid === null ? 'NULL' : ":'mid'";
  return JSON.parse(execFileSync('psql', args, {
    encoding: 'utf8',
    input: "SET ROLE session_search_reader; SELECT session_search.read_generation_session_v1(:'generation',:'session',:'ordinal'::integer," + midSql + ",:'offset'::integer,:'limit'::integer,:'chars'::integer);",
  }));
}, {authToken: 'fixture-shard'});
const router = makeRouterHandler({
  catalog: [{shard_id: 'chatgpt-0001', provider: 'chatgpt', state: 'READY',
    generation_id: input.generation_id, url: 'https://fixture.invalid/search'}],
  authToken: 'fixture-router', shardAuthToken: 'fixture-shard',
  fetchImpl: (url, init) => shard.fetch(new Request(url, init)),
});
const response = await router.fetch(new Request('https://fixture.invalid/mcp', {
  method: 'POST',
  headers: {authorization: 'Bearer fixture-router', 'content-type': 'application/json'},
  body: JSON.stringify({jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: {name: 'session_browse', arguments: input}}),
}));
assert.equal(response.status, 200);
const result = (await response.json()).result;
assert.equal(result.isError, false, JSON.stringify(result));
process.stdout.write(JSON.stringify(result.structuredContent));
