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

const routerFor = (c, fetchImpl) => makeRouterHandler({
  catalog: c, fetchImpl, authToken: RTOKEN, shardAuthToken: STOKEN,
});

test('router dispatch pins per-shard catalog generation and preserves bounded request arguments', async () => {
  const seen = [];
  const r = routerFor(catalog(A), async (_url, init) => {
    const payload = JSON.parse(init.body); seen.push(payload);
    return Response.json({ status: 'NO_SIGNAL', generation_id: A,
      generation_binding: 'router_pinned_v1', hits: [] });
  });
  const result = await ask(r, {
    query: ' needle ', search_mode: 'recall', limit: 5, scopes: ['trace'],
    session_id: 's', from_time: 2, to_time: 6,
  });
  assert.equal(result.status, 'NO_SIGNAL');
  assert.deepEqual(seen, [{
    query: 'needle', scopes: ['trace'], limit: 5, search_mode: 'recall',
    session_id: 's', from_time: 2, to_time: 6, generation_id: A,
  }]);
});

test('legacy ACTIVE-reading shard with matching generation is still rejected for missing witness', async () => {
  const oldShard = async () => Response.json({
    status: 'FOUND', generation_id: A,
    hits: [{ session_id: 's', message_id: 'm', text: 'old hit' }],
  });
  const result = await ask(routerFor(catalog(A), oldShard));
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(result.hits, []);
  assert.deepEqual(result.unavailable_shards, [
    { shard_id: 's1', reason: 'PINNING_NOT_CONFIRMED' },
  ]);
});

test('wrong/empty witness rejects a legacy shard even if generation matches', async () => {
  for (const witness of [null, '', 'legacy_active_v1', false, 1]) {
    const result = await ask(routerFor(catalog(A), async () => Response.json({
      status: 'FOUND', generation_id: A, generation_binding: witness,
      hits: [{ session_id: 's', message_id: 'm', text: 'wrong witness' }],
    })));
    assert.equal(result.status, 'PARTIAL');
    assert.deepEqual(result.hits, []);
  }
});

test('real transitional shard accepts pinned A even while legacy ACTIVE would be B', async () => {
  const seen = [];
  const shard = makeShardHandler(async (sql) => {
    seen.push(sql);
    return { status: 'FOUND', shard_id: 's1', generation_id: sql.values[0],
      hits: [{ session_id: 's', message_id: 'm', text: 'pinned' }] };
  }, { authToken: STOKEN });
  const r = routerFor(catalog(A), (url, init) => shard.fetch(
    new Request(url, { method: 'POST', headers: init.headers, body: init.body })));
  const result = await ask(r);
  assert.equal(result.status, 'FOUND');
  assert.equal(seen.length, 1);
  assert.match(seen[0].text, /search_generation_v2/);
  assert.equal(seen[0].values[0], A);
  assert.equal(result.hits[0].generation_id, A);
  assert.equal(result.hits[0].shard_id, 's1');
  assert.equal(result.hits[0].provider, 'synthetic');
});

test('router preserves healthy hits and PARTIAL when only one shard lacks witness', async () => {
  const r = routerFor([...catalog(A, 's1'), ...catalog(B, 's2')], async (url) => {
    const legacy = url.includes('s2');
    return Response.json({
      status: 'FOUND', generation_id: legacy ? B : A,
      ...(legacy ? {} : { generation_binding: 'router_pinned_v1' }),
      hits: [{ session_id: 's', message_id: 'm', ordinal: 1, text: 'hit' }],
    });
  });
  const result = await ask(r);
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(result.hits.map(x => x.shard_id), ['s1']);
  assert.deepEqual(result.unavailable_shards, [
    { shard_id: 's2', reason: 'PINNING_NOT_CONFIRMED' },
  ]);
});

test('router rejects contradictory shard identity and provider even with correct witness', async () => {
  for (const [payload, reason] of [
    [{ shard_id: 'other-shard' }, 'SHARD_ID_MISMATCH'],
    [{ provider: 'other-provider' }, 'PROVIDER_MISMATCH'],
  ]) {
    const r = routerFor(catalog(A), async () => Response.json({
      status: 'FOUND', generation_id: A, generation_binding: 'router_pinned_v1',
      ...payload,
      hits: [{ session_id: 's', message_id: 'm', text: 'wrong origin' }],
    }));
    const result = await ask(r);
    assert.equal(result.status, 'PARTIAL');
    assert.deepEqual(result.hits, []);
    assert.deepEqual(result.unavailable_shards, [{ shard_id: 's1', reason }]);
  }
});

test('auto recall is never triggered to hide a strict PARTIAL missing binding', async () => {
  const modes = [];
  const r = routerFor(catalog(A), async (_, init) => {
    modes.push(JSON.parse(init.body).search_mode);
    return Response.json({ status: 'NO_SIGNAL', generation_id: A, hits: [] });
  });
  const result = await ask(r, { query: 'no-signal', search_mode: 'auto' });
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(modes, ['strict']);
  assert.equal(result.retrieval.effective_mode, 'strict');
});

test('auto recall uses same generation and requires binding on both phases', async () => {
  const seen = [];
  const r = routerFor(catalog(A), async (_, init) => {
    const req = JSON.parse(init.body);
    seen.push([req.search_mode, req.generation_id]);
    return Response.json({ status: req.search_mode === 'strict' ? 'NO_SIGNAL' : 'FOUND',
      generation_id: A, generation_binding: 'router_pinned_v1',
      hits: req.search_mode === 'strict' ? [] : [{ session_id: 's', message_id: 'm', text: 'ok' }],
    });
  });
  const result = await ask(r, { query: 'need recall', search_mode: 'auto' }, '/mcp');
  assert.equal(result.status, 'FOUND');
  assert.equal(result.retrieval.effective_mode, 'recall');
  assert.deepEqual(seen, [['strict', A], ['recall', A]]);
});

test('catalog-only data rollback B to A does not require changing ACTIVE', async () => {
  const calls = [];
  const shard = makeShardHandler(async sql => {
    const generation_id = sql.values[0];
    calls.push(generation_id);
    return { status: 'FOUND', shard_id: 's1', generation_id,
      hits: [{ session_id: generation_id, message_id: 'm', text: 'ok' }] };
  }, { authToken: STOKEN });
  const fetchImpl = (url, init) => shard.fetch(new Request(url, {
    method: 'POST', headers: init.headers, body: init.body,
  }));
  const resultB = await ask(routerFor(catalog(B), fetchImpl));
  const resultA = await ask(routerFor(catalog(A), fetchImpl));
  assert.equal(resultB.status, 'FOUND');
  assert.equal(resultA.status, 'FOUND');
  assert.deepEqual(calls, [B, A]);
  assert.deepEqual(resultA.hits.map(x => x.generation_id), [A]);
});

test('unavailable exact generation stays PARTIAL; never silently replaces with ACTIVE', async () => {
  const shard = makeShardHandler(async () => { throw Error('generation not READY'); },
    { authToken: STOKEN });
  const fetchImpl = (url, init) => shard.fetch(new Request(url, {
    method: 'POST', headers: init.headers, body: init.body,
  }));
  const result = await ask(routerFor(catalog(A), fetchImpl));
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(result.hits, []);
  assert.deepEqual(result.unavailable_shards, [
    { shard_id: 's1', reason: 'QUERY_FAILED' },
  ]);
});

// Version-matrix checks represent old routers by their unchanged HTTP payload,
// without vendoring a second implementation of the legacy router.
test('S1: old-router request still works against transitional shard via ACTIVE SQL', async () => {
  const seen = [];
  const shard = makeShardHandler(async sql => {
    seen.push(sql);
    return { status: 'FOUND', shard_id: 's1', generation_id: A,
      hits: [{ session_id: 'legacy', message_id: 'm', text: 'old' }] };
  }, { authToken: STOKEN });
  const response = await shard.fetch(new Request('https://s1.test/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: 'Bearer ' + STOKEN },
    body: JSON.stringify({ query: 'legacy', search_mode: 'strict' }),
  }));
  assert.equal(response.status, 200);
  assert.match(seen[0].text, /session_search\.search_v2\(/);
  assert.deepEqual((await response.json()).generation_binding, undefined);
});

test('S2: pinned router chooses B while transitional shard legacy ACTIVE remains A', async () => {
  const seen = [];
  const shard = makeShardHandler(async sql => {
    seen.push(sql);
    const pinned = sql.text.includes('search_generation_v2');
    const generation_id = pinned ? sql.values[0] : A;
    return { status: 'FOUND', shard_id: 's1', generation_id,
      hits: [{ session_id: generation_id, message_id: 'm', text: 'hit' }] };
  }, { authToken: STOKEN });
  const fetchImpl = (url, init) => shard.fetch(new Request(url, {
    method: 'POST', headers: init.headers, body: init.body,
  }));
  const pinnedB = await ask(routerFor(catalog(B), fetchImpl));
  assert.equal(pinnedB.status, 'FOUND');
  assert.deepEqual(pinnedB.hits.map(h => h.generation_id), [B]);
  assert.match(seen[0].text, /search_generation_v2\(/);

  const legacyResponse = await shard.fetch(new Request('https://s1.test/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: 'Bearer ' + STOKEN },
    body: JSON.stringify({ query: 'legacy' }),
  }));
  assert.equal((await legacyResponse.json()).generation_id, A);
  assert.match(seen[1].text, /search_v2\(/);
});

test('S2 interleaving: in-flight catalog A remains pinned as concurrent router B finishes', async () => {
  let releaseA;
  let signalledA;
  const startedA = new Promise(resolve => { signalledA = resolve; });
  const holdA = new Promise(resolve => { releaseA = resolve; });
  const seen = [];
  const fakeFetch = async (_url, init) => {
    const request = JSON.parse(init.body);
    seen.push(request.generation_id);
    if (request.generation_id === A) {
      signalledA();
      await holdA;
    }
    return Response.json({
      status: 'FOUND', generation_binding: 'router_pinned_v1',
      generation_id: request.generation_id,
      hits: [{ session_id: request.generation_id, message_id: 'm', text: 'hit' }],
    });
  };
  const pendingA = ask(routerFor(catalog(A), fakeFetch));
  await startedA;
  const outcomeB = await ask(routerFor(catalog(B), fakeFetch));
  releaseA();
  const outcomeA = await pendingA;
  assert.deepEqual(seen, [A, B]);
  assert.equal(outcomeA.status, 'FOUND');
  assert.equal(outcomeB.status, 'FOUND');
  assert.deepEqual(outcomeA.hits.map(x => x.generation_id), [A]);
  assert.deepEqual(outcomeB.hits.map(x => x.generation_id), [B]);
});

test('S4: a pinned-only shard rejects legacy payload, but works through pinned router', async () => {
  const strictOnlyFetch = async (_url, init) => {
    const request = JSON.parse(init.body);
    if (!Object.hasOwn(request, 'generation_id')) {
      return Response.json({ error: 'GENERATION_OUT_OF_BOUNDS' }, { status: 400 });
    }
    return Response.json({
      status: 'NO_SIGNAL', generation_binding: 'router_pinned_v1',
      generation_id: request.generation_id, hits: [],
    });
  };
  const legacy = await strictOnlyFetch('https://s1.test/search', {
    body: JSON.stringify({ query: 'legacy' }),
  });
  assert.equal(legacy.status, 400);
  const current = await ask(routerFor(catalog(A), strictOnlyFetch));
  assert.equal(current.status, 'NO_SIGNAL');
  assert.deepEqual(current.unavailable_shards, []);
});
