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

function groupedResult(generationId, groups = []) {
  return {
    status: groups.length ? 'FOUND' : 'NO_SIGNAL',
    generation_id: generationId,
    shard_id: 'synthetic-0001',
    provider: 'synthetic',
    grouping: 'exact_text_v1',
    candidate_count: groups.reduce((sum, group) => sum + group.occurrence_count, 0),
    returned_group_count: groups.length,
    groups,
  };
}

test('explicit grouped_v1 shard request uses active-generation grouped v3', async () => {
  const calls = [];
  const shard = makeShardHandler(async (call) => {
    calls.push(call);
    return groupedResult(A);
  }, { authToken: TOKEN });
  const response = await shard.fetch(makeRequest({
    query: 'copper compass', scopes: ['dialogue'], limit: 2, result_shape: 'grouped_v1',
  }));
  assert.equal(response.status, 200);
  assert.deepEqual(calls, [{
    text: 'SELECT session_search.search_grouped_v3($1,$2::text[],$3,$4,$5,$6,$7,$8) AS result',
    values: ['copper compass', ['dialogue'], 2, 6, null, null, null, 'strict'],
  }]);
  const result = await response.json();
  assert.equal(result.grouping, 'exact_text_v1');
  assert.deepEqual(result.groups, []);
});

test('flat result remains an explicit internal rollback seam', async () => {
  const calls = [];
  const shard = makeShardHandler(async (call) => {
    calls.push(call);
    return { status: 'FOUND', generation_id: A, shard_id: 'synthetic-0001', hits: [] };
  }, { authToken: TOKEN });
  const response = await shard.fetch(makeRequest({
    query: 'legacy', result_shape: 'flat_v1',
  }));
  assert.equal(response.status, 200);
  assert.match(calls[0].text, /session_search\.search_v2\(/);
  assert.deepEqual((await response.json()).hits, []);
});

test('explicit pinned default selects grouped v3 and preserves strict or recall mode', async () => {
  const calls = [];
  const shard = makeShardHandler(async (call) => {
    calls.push(call);
    return groupedResult(B);
  }, { authToken: TOKEN });
  for (const mode of ['strict', 'recall']) {
    const response = await shard.fetch(makeRequest({
      query: 'needle', generation_id: B, search_mode: mode, limit: 7, result_shape: 'grouped_v1',
      session_id: 's', from_time: 1, to_time: 3,
    }));
    assert.equal(response.status, 200);
    assert.equal((await response.json()).generation_binding, 'router_pinned_v1');
  }
  assert.equal(calls.length, 2);
  assert.match(calls[0].text, /search_generation_grouped_v3/);
  assert.deepEqual(calls[0].values, [
    B, 'needle', ['dialogue', 'evidence'], 7, 21, 's', 1, 3, 'strict',
  ]);
  assert.equal(calls[1].values.at(-1), 'recall');
});

test('flat pinned rollback still uses exact generation v2', () => {
  const call = buildSearchCall({
    generation_id: B, query: 'copper compass', scopes: ['trace'], limit: 7,
    session_id: 's', from_time: 1, to_time: 3, search_mode: 'recall',
  });
  assert.equal(
    call.text,
    'SELECT session_search.search_generation_v2($1,$2,$3::text[],$4,$5,$6,$7,$8) AS result',
  );
  assert.deepEqual(call.values, [B, 'copper compass', ['trace'], 7, 's', 1, 3, 'recall']);
});

test('pinned response carries verified protocol witness only after exact-generation execution', async () => {
  const calls = [];
  const shard = makeShardHandler(async (call) => {
    calls.push(call);
    return { ...groupedResult(B), generation_binding: 'forged-from-storage' };
  }, { authToken: TOKEN });
  const response = await shard.fetch(makeRequest({
    query: 'needle', generation_id: B, generation_binding: 'forged-by-client', result_shape: 'grouped_v1',
  }));
  assert.equal(response.status, 200);
  assert.equal(calls.length, 1);
  assert.match(calls[0].text, /session_search\.search_generation_grouped_v3/);
  const result = await response.json();
  assert.equal(result.generation_binding, 'router_pinned_v1');
  assert.equal(result.generation_id, B);
});

test('default grouped shard path fails closed for invalid internal controls without SQL', async () => {
  for (const [body, expected] of [
    [{ query: 'needle', result_shape: 'semantic' }, 'RESULT_SHAPE_OUT_OF_BOUNDS'],
    [{ query: 'needle', result_shape: 'flat_v1', candidate_budget: 6 }, 'CANDIDATE_BUDGET_REQUIRES_GROUPED_RESULT'],
    [{ query: 'needle', grouping: 'semantic' }, 'GROUPING_CONTROL_INVALID'],
    [{ query: 'needle', limit: 10, candidate_budget: 9, result_shape: 'grouped_v1' }, 'CANDIDATE_BUDGET_OUT_OF_BOUNDS'],
  ]) {
    let calls = 0;
    const shard = makeShardHandler(async () => { calls += 1; }, { authToken: TOKEN });
    const response = await shard.fetch(makeRequest(body));
    assert.equal(response.status, 400, expected);
    assert.equal((await response.json()).error, expected);
    assert.equal(calls, 0);
  }
});

test('explicit null, empty, malformed, uppercase or non-string generation never falls back to ACTIVE', async () => {
  let calls = 0;
  const shard = makeShardHandler(async () => { calls += 1; return groupedResult(A); }, { authToken: TOKEN });
  for (const invalid of [null, '', 'not-a-sha', 'F'.repeat(64), 42, [], {}, 'a'.repeat(63), 'g'.repeat(64)]) {
    const response = await shard.fetch(makeRequest({ query: 'needle', generation_id: invalid }));
    assert.equal(response.status, 400, JSON.stringify(invalid));
    assert.equal((await response.json()).error, 'GENERATION_OUT_OF_BOUNDS');
  }
  assert.equal(calls, 0);
});

test('mismatched SQL generation cannot produce pinned witness', async () => {
  const shard = makeShardHandler(async () => groupedResult(A), { authToken: TOKEN });
  const response = await shard.fetch(makeRequest({ query: 'needle', generation_id: B }));
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: 'QUERY_FAILED' });
});

test('SQL failure on pinned READY lookup remains explicit and never returns witness or reads ACTIVE', async () => {
  let called;
  const shard = makeShardHandler(async (sql) => {
    called = sql;
    throw new Error('generation not READY');
  }, { authToken: TOKEN });
  const response = await shard.fetch(makeRequest({ query: 'needle', generation_id: B, result_shape: 'grouped_v1' }));
  assert.match(called.text, /session_search\.search_generation_grouped_v3/);
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
