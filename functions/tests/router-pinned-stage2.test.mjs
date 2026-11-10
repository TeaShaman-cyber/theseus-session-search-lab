import test from 'node:test';
import assert from 'node:assert/strict';
import { makeRouterHandler } from '../router/handler.mjs';
import { makeShardHandler } from '../shard/handler.mjs';

const A = 'a'.repeat(64);
const B = 'b'.repeat(64);
const RTOKEN = 'synthetic-router-token';
const STOKEN = 'synthetic-shard-token';

const catalog = (generation_id, shard_id = 's1') => [{
  shard_id, provider: 'synthetic', generation_id, state: 'READY',
  url: 'https://' + shard_id + '.test/search',
}];

function groupedBody(generationId, {
  text = null,
  sessionId = 's',
  messageId = 'm',
  shardId,
  provider,
  witness = 'router_pinned_v1',
  omitWitness = false,
} = {}) {
  const groups = text == null ? [] : [{
    text,
    representative: { session_id: sessionId, message_id: messageId, ordinal: 1 },
    occurrence_count: 1,
    occurrences: [{ session_id: sessionId, message_id: messageId, ordinal: 1 }],
  }];
  return {
    status: groups.length ? 'FOUND' : 'NO_SIGNAL',
    generation_id: generationId,
    ...(omitWitness ? {} : { generation_binding: witness }),
    ...(shardId ? { shard_id: shardId } : {}),
    ...(provider ? { provider } : {}),
    grouping: 'exact_text_v1',
    candidate_count: groups.length,
    returned_group_count: groups.length,
    groups,
  };
}

async function ask(router, input = { query: 'synthetic' }, path = '/search') {
  const response = await router.fetch(new Request('https://router.test' + path, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: 'Bearer ' + RTOKEN },
    body: path === '/mcp' ? JSON.stringify({
      jsonrpc: '2.0', id: 9, method: 'tools/call', params: {
        name: 'session_search', arguments: input,
      },
    }) : JSON.stringify(input),
  }));
  assert.equal(response.status, 200);
  const result = await response.json();
  return path === '/mcp' ? result.result.structuredContent : result;
}

const routerFor = (c, fetchImpl, options = {}) => makeRouterHandler({
  catalog: c, fetchImpl, authToken: RTOKEN, shardAuthToken: STOKEN, ...options,
});

test('router pins generation and sends grouped default with bounded candidate budget', async () => {
  const seen = [];
  const r = routerFor(catalog(A), async (_url, init) => {
    const payload = JSON.parse(init.body);
    seen.push(payload);
    return Response.json(groupedBody(A));
  });
  const result = await ask(r, {
    query: ' needle ', search_mode: 'recall', limit: 5, scopes: ['trace'],
    session_id: 's', from_time: 2, to_time: 6,
  });
  assert.equal(result.status, 'NO_SIGNAL');
  assert.deepEqual(seen, [{
    query: 'needle', generation_id: A, scopes: ['trace'], limit: 5,
    search_mode: 'recall', session_id: 's', from_time: 2, to_time: 6,
    result_shape: 'grouped_v1', candidate_budget: 15,
  }]);
});

test('missing pinning witness stays PARTIAL with no groups', async () => {
  const oldShard = async () => Response.json(groupedBody(A, {
    text: 'old hit', omitWitness: true,
  }));
  const result = await ask(routerFor(catalog(A), oldShard));
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(result.groups, []);
  assert.deepEqual(result.unavailable_shards, [
    { shard_id: 's1', reason: 'PINNING_NOT_CONFIRMED' },
  ]);
});

test('wrong pinning witness is rejected even when generation matches', async () => {
  for (const witness of [null, '', 'legacy_active_v1', false, 1]) {
    const result = await ask(routerFor(catalog(A), async () => Response.json(
      groupedBody(A, { text: 'wrong witness', witness }),
    )));
    assert.equal(result.status, 'PARTIAL');
    assert.deepEqual(result.groups, []);
  }
});

test('real shard executes exact grouped generation and router preserves provenance', async () => {
  const seen = [];
  const shard = makeShardHandler(async (sql) => {
    seen.push(sql);
    return groupedBody(sql.values[0], { text: 'pinned', omitWitness: true });
  }, { authToken: STOKEN });
  const r = routerFor(catalog(A), (url, init) => shard.fetch(
    new Request(url, { method: 'POST', headers: init.headers, body: init.body }),
  ));
  const result = await ask(r);
  assert.equal(result.status, 'FOUND');
  assert.equal(seen.length, 1);
  assert.match(seen[0].text, /search_generation_grouped_v3/);
  assert.equal(seen[0].values[0], A);
  assert.equal(result.groups[0].representative.generation_id, A);
  assert.equal(result.groups[0].representative.shard_id, 's1');
  assert.equal(result.groups[0].representative.provider, 'synthetic');
});

test('router preserves healthy groups and PARTIAL when one shard lacks witness', async () => {
  const r = routerFor([...catalog(A, 's1'), ...catalog(B, 's2')], async (url) => {
    const legacy = url.includes('s2');
    return Response.json(groupedBody(legacy ? B : A, {
      text: 'hit',
      witness: 'router_pinned_v1', omitWitness: legacy,
    }));
  });
  const result = await ask(r);
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(result.groups.map((x) => x.representative.shard_id), ['s1']);
  assert.deepEqual(result.unavailable_shards, [
    { shard_id: 's2', reason: 'PINNING_NOT_CONFIRMED' },
  ]);
});

test('router rejects contradictory shard identity and provider with correct witness', async () => {
  for (const [payload, reason] of [
    [{ shard_id: 'other-shard' }, 'SHARD_ID_MISMATCH'],
    [{ provider: 'other-provider' }, 'PROVIDER_MISMATCH'],
  ]) {
    const r = routerFor(catalog(A), async () => Response.json({
      ...groupedBody(A, { text: 'wrong origin' }),
      ...payload,
    }));
    const result = await ask(r);
    assert.equal(result.status, 'PARTIAL');
    assert.deepEqual(result.groups, []);
    assert.deepEqual(result.unavailable_shards, [{ shard_id: 's1', reason }]);
  }
});

test('auto recall never hides strict PARTIAL missing binding', async () => {
  const modes = [];
  const r = routerFor(catalog(A), async (_, init) => {
    modes.push(JSON.parse(init.body).search_mode);
    return Response.json(groupedBody(A, { omitWitness: true }));
  });
  const result = await ask(r, { query: 'no-signal', search_mode: 'auto' });
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(modes, ['strict']);
  assert.equal(result.retrieval.effective_mode, 'strict');
});

test('auto recall uses same generation on strict and recall grouped phases', async () => {
  const seen = [];
  const r = routerFor(catalog(A), async (_, init) => {
    const req = JSON.parse(init.body);
    seen.push([req.search_mode, req.generation_id, req.result_shape]);
    return Response.json(groupedBody(A, {
      text: req.search_mode === 'strict' ? null : 'ok',
    }));
  });
  const result = await ask(r, { query: 'need recall', search_mode: 'auto' }, '/mcp');
  assert.equal(result.status, 'FOUND');
  assert.equal(result.retrieval.effective_mode, 'recall');
  assert.deepEqual(seen, [
    ['strict', A, 'grouped_v1'],
    ['recall', A, 'grouped_v1'],
  ]);
});

test('catalog-only data rollback B to A keeps exact grouped generation pinning', async () => {
  const calls = [];
  const shard = makeShardHandler(async (sql) => {
    const generationId = sql.values[0];
    calls.push(generationId);
    return groupedBody(generationId, { text: 'ok', omitWitness: true });
  }, { authToken: STOKEN });
  const fetchImpl = (url, init) => shard.fetch(new Request(url, {
    method: 'POST', headers: init.headers, body: init.body,
  }));
  const resultB = await ask(routerFor(catalog(B), fetchImpl));
  const resultA = await ask(routerFor(catalog(A), fetchImpl));
  assert.equal(resultB.status, 'FOUND');
  assert.equal(resultA.status, 'FOUND');
  assert.deepEqual(calls, [B, A]);
  assert.deepEqual(resultA.groups.map((x) => x.representative.generation_id), [A]);
});

test('unavailable exact generation stays PARTIAL and never falls back to ACTIVE', async () => {
  const shard = makeShardHandler(async () => { throw Error('generation not READY'); },
    { authToken: STOKEN });
  const fetchImpl = (url, init) => shard.fetch(new Request(url, {
    method: 'POST', headers: init.headers, body: init.body,
  }));
  const result = await ask(routerFor(catalog(A), fetchImpl));
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(result.groups, []);
  assert.deepEqual(result.unavailable_shards, [
    { shard_id: 's1', reason: 'QUERY_FAILED' },
  ]);
});

test('un-pinned direct shard request without shape remains flat for old-router compatibility', async () => {
  const seen = [];
  const shard = makeShardHandler(async (sql) => {
    seen.push(sql);
    return { status: 'FOUND', shard_id: 's1', generation_id: A, hits: [{ text: 'old' }] };
  }, { authToken: STOKEN });
  const response = await shard.fetch(new Request('https://s1.test/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: 'Bearer ' + STOKEN },
    body: JSON.stringify({ query: 'legacy', search_mode: 'strict' }),
  }));
  assert.equal(response.status, 200);
  assert.match(seen[0].text, /session_search\.search_v2\(/);
  const result = await response.json();
  assert.equal(result.generation_binding, undefined);
  assert.deepEqual(result.hits, [{ text: 'old' }]);
});

test('pinned router and ACTIVE direct request remain independent', async () => {
  const seen = [];
  const shard = makeShardHandler(async (sql) => {
    seen.push(sql);
    const pinned = sql.text.includes('search_generation_grouped_v3');
    if (pinned) {
      return groupedBody(sql.values[0], { text: 'hit', omitWitness: true });
    }
    return { status: 'FOUND', shard_id: 's1', generation_id: A, hits: [{ text: 'hit' }] };
  }, { authToken: STOKEN });
  const fetchImpl = (url, init) => shard.fetch(new Request(url, {
    method: 'POST', headers: init.headers, body: init.body,
  }));
  const pinnedB = await ask(routerFor(catalog(B), fetchImpl));
  assert.equal(pinnedB.status, 'FOUND');
  assert.deepEqual(pinnedB.groups.map((g) => g.representative.generation_id), [B]);
  assert.match(seen[0].text, /search_generation_grouped_v3\(/);

  const activeResponse = await shard.fetch(new Request('https://s1.test/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: 'Bearer ' + STOKEN },
    body: JSON.stringify({ query: 'legacy' }),
  }));
  assert.equal((await activeResponse.json()).generation_id, A);
  assert.match(seen[1].text, /search_v2\(/);
});

test('interleaving requests preserve their catalog-pinned generations', async () => {
  let releaseA;
  let signalledA;
  const startedA = new Promise((resolve) => { signalledA = resolve; });
  const holdA = new Promise((resolve) => { releaseA = resolve; });
  const seen = [];
  const fakeFetch = async (_url, init) => {
    const request = JSON.parse(init.body);
    seen.push(request.generation_id);
    if (request.generation_id === A) {
      signalledA();
      await holdA;
    }
    return Response.json(groupedBody(request.generation_id, { text: 'hit' }));
  };
  const pendingA = ask(routerFor(catalog(A), fakeFetch));
  await startedA;
  const outcomeB = await ask(routerFor(catalog(B), fakeFetch));
  releaseA();
  const outcomeA = await pendingA;
  assert.deepEqual(seen, [A, B]);
  assert.equal(outcomeA.status, 'FOUND');
  assert.equal(outcomeB.status, 'FOUND');
  assert.deepEqual(outcomeA.groups.map((x) => x.representative.generation_id), [A]);
  assert.deepEqual(outcomeB.groups.map((x) => x.representative.generation_id), [B]);
});

test('pinned-only shard can reject legacy payload while current grouped router succeeds', async () => {
  const strictOnlyFetch = async (_url, init) => {
    const request = JSON.parse(init.body);
    if (!Object.hasOwn(request, 'generation_id')) {
      return Response.json({ error: 'GENERATION_OUT_OF_BOUNDS' }, { status: 400 });
    }
    return Response.json(groupedBody(request.generation_id));
  };
  const legacy = await strictOnlyFetch('https://s1.test/search', {
    body: JSON.stringify({ query: 'legacy' }),
  });
  assert.equal(legacy.status, 400);
  const current = await ask(routerFor(catalog(A), strictOnlyFetch));
  assert.equal(current.status, 'NO_SIGNAL');
  assert.deepEqual(current.unavailable_shards, []);
});
