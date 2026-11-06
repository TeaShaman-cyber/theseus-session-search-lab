import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSearchCall } from '../shard/core.mjs';
import { makeShardHandler } from '../shard/handler.mjs';

const A = 'a'.repeat(64);
const B = 'b'.repeat(64);
const TOKEN = 'synthetic-shard-only-token';

function makeRequest(body, authorized = true) {
  return new Request('https://shard.test/search', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      ...(authorized ? { authorization: 'Bearer ' + TOKEN } : {}),
    },
    body: JSON.stringify(body),
  });
}

test('legacy request uses active-generation SQL and original response shape', async () => {
  const calls = [];
  const shard = makeShardHandler(async (call) => {
    calls.push(call);
    return { status: 'FOUND', generation_id: A, shard_id: 'synthetic-0001', hits: [] };
  }, { authToken: TOKEN });
  const response = await shard.fetch(makeRequest({ query: 'copper compass', scopes: ['dialogue'], limit: 2 }));
  assert.equal(response.status, 200);
  assert.deepEqual(calls, [{
    text: 'SELECT session_search.search_v2($1,$2::text[],$3,$4,$5,$6,$7) AS result',
    values: ['copper compass', ['dialogue'], 2, null, null, null, 'strict'],
  }]);
  assert.deepEqual(await response.json(), {
    status: 'FOUND', generation_id: A, shard_id: 'synthetic-0001', hits: [],
  });
});

test('explicit pinned ID selects exact READY SQL function and bind order, preserving recall mode', () => {
  const call = buildSearchCall({
    generation_id: B, query: '  copper compass  ', scopes: ['trace'], limit: 7,
    session_id: 's', from_time: 1, to_time: 3, search_mode: 'recall',
  });
  assert.equal(call.text, 'SELECT session_search.search_generation_v2($1,$2,$3::text[],$4,$5,$6,$7,$8) AS result');
  assert.deepEqual(call.values, [B, 'copper compass', ['trace'], 7, 's', 1, 3, 'recall']);
});

test('pinned response carries verified protocol witness only after exact-generation execution', async () => {
  const calls = [];
  const shard = makeShardHandler(async (call) => {
    calls.push(call);
    return {
      status: 'FOUND', shard_id: 'synthetic-0001', generation_id: B, hits: [],
      generation_binding: 'forged-from-storage',
    };
  }, { authToken: TOKEN });
  const response = await shard.fetch(makeRequest({ query: 'needle', generation_id: B, generation_binding: 'forged-by-client' }));
  assert.equal(response.status, 200);
  assert.equal(calls.length, 1);
  assert.match(calls[0].text, /session_search\.search_generation_v2/);
  const result = await response.json();
  assert.equal(result.generation_binding, 'router_pinned_v1');
  assert.equal(result.generation_id, B);
});

test('explicit null, empty, malformed, uppercase or non-string generation never falls back to ACTIVE', async () => {
  let calls = 0;
  const shard = makeShardHandler(async () => { calls++; return { status: 'FOUND', generation_id: A, hits: [] }; }, { authToken: TOKEN });
  for (const invalid of [null, '', 'not-a-sha', 'F'.repeat(64), 42, [], {}, 'a'.repeat(63), 'g'.repeat(64)]) {
    const response = await shard.fetch(makeRequest({ query: 'needle', generation_id: invalid }));
    assert.equal(response.status, 400, JSON.stringify(invalid));
    assert.equal((await response.json()).error, 'GENERATION_OUT_OF_BOUNDS');
  }
  assert.equal(calls, 0);
});

test('mismatched SQL generation cannot produce pinned witness', async () => {
  const shard = makeShardHandler(async () => ({
    status: 'FOUND', shard_id: 'synthetic-0001', generation_id: A, hits: [],
  }), { authToken: TOKEN });
  const response = await shard.fetch(makeRequest({ query: 'needle', generation_id: B }));
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: 'QUERY_FAILED' });
});

test('SQL failure on pinned READY lookup remains explicit and never returns witness or reads ACTIVE', async () => {
  let called;
  const shard = makeShardHandler(async (sql) => { called = sql; throw new Error('generation not READY'); }, { authToken: TOKEN });
  const response = await shard.fetch(makeRequest({ query: 'needle', generation_id: B }));
  assert.match(called.text, /session_search\.search_generation_v2/);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: 'QUERY_FAILED' });
});

test('shard bearer auth remains enforced before pinned SQL executes', async () => {
  let called = false;
  const shard = makeShardHandler(async () => { called = true; }, { authToken: TOKEN });
  const response = await shard.fetch(makeRequest({ query: 'needle', generation_id: B }, false));
  assert.equal(response.status, 401);
  assert.equal(called, false);
});
