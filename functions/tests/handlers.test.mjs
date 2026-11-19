import test from 'node:test';
import assert from 'node:assert/strict';

import { makeShardHandler as makeShardHandlerImpl } from '../shard/handler.mjs';
import { makeRouterHandler as makeRouterHandlerImpl } from '../router/handler.mjs';

const ROUTER_TOKEN = 'router-test-token';
const SHARD_TOKEN = 'shard-test-token';

function withBearer(handler, token) {
  return {
    async fetch(request) {
      const headers = new Headers(request.headers);
      headers.set('authorization', 'Bearer ' + token);
      return handler.fetch(new Request(request, { headers }));
    },
  };
}

function makeShardHandler(executeSearch, options = {}) {
  return withBearer(
    makeShardHandlerImpl(executeSearch, { ...options, authToken: SHARD_TOKEN }),
    SHARD_TOKEN,
  );
}

function groupedPayload({
  generationId,
  shardId,
  provider,
  text = null,
  sessionId = 's',
  messageId = 'm',
  ordinal = 1,
}) {
  const groups = text == null ? [] : [{
    text,
    representative: { session_id: sessionId, message_id: messageId, ordinal },
    occurrence_count: 1,
    occurrences: [{ session_id: sessionId, message_id: messageId, ordinal }],
  }];
  return {
    status: groups.length ? 'FOUND' : 'NO_SIGNAL',
    generation_binding: 'router_pinned_v1',
    generation_id: generationId,
    ...(shardId ? { shard_id: shardId } : {}),
    ...(provider ? { provider } : {}),
    grouping: 'exact_text_v1',
    candidate_count: groups.length,
    returned_group_count: groups.length,
    groups,
  };
}

function makeRouterHandler(options) {
  return withBearer(
    makeRouterHandlerImpl({
      ...options,
      authToken: ROUTER_TOKEN,
      shardAuthToken: SHARD_TOKEN,
    }),
    ROUTER_TOKEN,
  );
}


test('shard keeps missing result_shape flat for rolling-upgrade compatibility', async () => {
  const calls = [];
  const handler = makeShardHandler(async (call) => {
    calls.push(call);
    return { status: 'FOUND', generation_id: 'a'.repeat(64), hits: [{ message_id: 'c1' }] };
  });
  const response = await handler.fetch(new Request('https://shard.test/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'copper compass', scopes: ['dialogue'], limit: 8 }),
  }));
  assert.equal(response.status, 200);
  assert.equal(calls.length, 1);
  assert.match(calls[0].text, /search_v2/);
  assert.equal((await response.json()).hits[0].message_id, 'c1');
});

test('shard executes grouped v3 when the current router requests grouped_v1', async () => {
  const calls = [];
  const handler = makeShardHandler(async (call) => {
    calls.push(call);
    return groupedPayload({ generationId: 'a'.repeat(64), text: 'copper compass', messageId: 'c1' });
  });
  const response = await handler.fetch(new Request('https://shard.test/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      query: 'copper compass', scopes: ['dialogue'], limit: 8, result_shape: 'grouped_v1',
    }),
  }));
  assert.equal(response.status, 200);
  assert.equal(calls.length, 1);
  assert.match(calls[0].text, /search_grouped_v3/);
  const result = await response.json();
  assert.equal(result.grouping, 'exact_text_v1');
  assert.equal(result.groups[0].text, 'copper compass');
});

test('shard logs redact database error messages', async () => {
  const logs = [];
  const original = console.error;
  console.error = (value) => logs.push(String(value));
  try {
    const handler = makeShardHandler(async () => {
      throw new Error('connection failed postgresql://secret-user:secret-pass@example.test/db');
    });
    const response = await handler.fetch(new Request('https://shard.test/search', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'needle' }),
    }));
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error, 'QUERY_FAILED');
  } finally {
    console.error = original;
  }
  assert.equal(logs.length, 1);
  assert.equal(logs[0].includes('postgresql://'), false);
  assert.equal(logs[0].includes('secret-pass'), false);
});

test('router HTTP adapter defaults to exact-text groups across shards', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
    { shard_id: 'hermes-0001', provider: 'hermes', generation_id: 'b'.repeat(64), state: 'READY', url: 'https://b.test/search' },
  ];
  const seen = [];
  const fakeFetch = async (url, init) => {
    seen.push(String(url));
    const request = JSON.parse(init.body);
    assert.equal(request.query, 'copper compass');
    assert.equal(request.result_shape, 'grouped_v1');
    assert.equal(request.candidate_budget, 24);
    const a = String(url).includes('a.test');
    return Response.json(groupedPayload({
      generationId: a ? 'a'.repeat(64) : 'b'.repeat(64),
      shardId: a ? 'chatgpt-0001' : 'hermes-0001',
      provider: a ? 'chatgpt' : 'hermes',
      text: 'shared',
      sessionId: a ? 'ca' : 'hb',
      messageId: a ? 'c1' : 'h1',
    }));
  };
  const handler = makeRouterHandler({ catalog, fetchImpl: fakeFetch });
  const response = await handler.fetch(new Request('https://router.test/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'copper compass', limit: 8 }),
  }));
  const result = await response.json();
  assert.equal(result.status, 'FOUND');
  assert.equal(result.grouping, 'exact_text_v1');
  assert.match(result.request_ref, /^[0-9a-f]{32}$/);
  assert.deepEqual(seen.sort(), ['https://a.test/search', 'https://b.test/search']);
  assert.equal(result.groups.length, 1);
  assert.equal(result.groups[0].text, 'shared');
  assert.equal(result.groups[0].occurrence_count, 2);
  assert.deepEqual(
    result.groups[0].occurrences.map((item) => item.shard_id).sort(),
    ['chatgpt-0001', 'hermes-0001'],
  );
});


test('time-scoped query outside catalog bounds returns explicit coverage without false absence', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', time_min: 100, time_max: 200, url: 'https://a.test/search' },
    { shard_id: 'hermes-0001', provider: 'hermes', generation_id: 'b'.repeat(64), state: 'READY', time_min: 1000, time_max: 2000, url: 'https://b.test/search' },
  ];
  let calls = 0;
  const handler = makeRouterHandler({ catalog, fetchImpl: async () => { calls += 1; throw Error('unexpected network call'); } });
  const response = await handler.fetch(new Request('https://router.test/search', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'the latest discussion', provider: 'chatgpt', from_time: 300, to_time: 400 }),
  }));
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.status, 'NO_SIGNAL'); // preserve legacy result contract
  assert.deepEqual(result.queried_shards, []);
  assert.equal(calls, 0);
  assert.deepEqual(result.coverage, {
    window_fit: 'OUTSIDE_OBSERVED_INDEX_RANGE',
    source_currentness: 'UNKNOWN_WITHOUT_SOURCE_WATERMARK',
    eligible_shard_count: 1,
    selected_shard_count: 0,
    catalog_observed_max_time: 200,
    catalog_bounds_complete: true,
  });
});

test('time-scoped searched miss differs from unobserved provider and unknown catalog bounds', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', time_min: 100, time_max: 500, url: 'https://a.test/search' },
    { shard_id: 'chatgpt-0002', provider: 'chatgpt', generation_id: 'b'.repeat(64), state: 'READY', url: 'https://b.test/search' },
  ];
  const handler = makeRouterHandler({ catalog, fetchImpl: async (url) => Response.json(groupedPayload({
    generationId: String(url).includes('a.test') ? 'a'.repeat(64) : 'b'.repeat(64),
  })) });
  const read = async (provider) => {
    const response = await handler.fetch(new Request('https://router.test/search', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'missing', provider, from_time: 300, to_time: 400 }),
    }));
    return await response.json();
  };
  const realMiss = await read('chatgpt');
  assert.equal(realMiss.status, 'NO_SIGNAL');
  assert.deepEqual(realMiss.queried_shards, ['chatgpt-0001', 'chatgpt-0002']);
  assert.equal(realMiss.coverage.window_fit, 'UNKNOWN_COVERAGE'); // second shard lacks time bounds
  assert.equal(realMiss.coverage.catalog_bounds_complete, false);
  assert.equal(realMiss.coverage.source_currentness, 'UNKNOWN_WITHOUT_SOURCE_WATERMARK');
  const wrongProvider = await read('claude');
  assert.deepEqual(wrongProvider.queried_shards, []);
  assert.equal(wrongProvider.coverage.window_fit, 'UNKNOWN_COVERAGE');
  assert.equal(wrongProvider.coverage.eligible_shard_count, 0);
  assert.equal(wrongProvider.coverage.catalog_observed_max_time, null);
});

test('searched bounded time window can be relevant but never claims upstream completeness', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', time_min: 100, time_max: 500, url: 'https://a.test/search' },
  ];
  let calls = 0;
  const handler = makeRouterHandler({ catalog, fetchImpl: async () => {
    calls += 1;
    return Response.json(groupedPayload({ generationId: 'a'.repeat(64) }));
  } });
  const search = async (body) => {
    const response = await handler.fetch(new Request('https://router.test/search', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
    }));
    assert.equal(response.status, 200);
    return await response.json();
  };
  const result = await search({ query: 'missing', provider: 'chatgpt', from_time: 300, to_time: 400 });
  assert.equal(result.status, 'NO_SIGNAL');
  assert.deepEqual(result.queried_shards, ['chatgpt-0001']);
  assert.deepEqual(result.coverage, {
    window_fit: 'OBSERVED_POSSIBLE',
    source_currentness: 'UNKNOWN_WITHOUT_SOURCE_WATERMARK',
    eligible_shard_count: 1,
    selected_shard_count: 1,
    catalog_observed_max_time: 500,
    catalog_bounds_complete: true,
  });
  const unscoped = await search({ query: 'missing', provider: 'chatgpt' });
  assert.equal(Object.hasOwn(unscoped, 'coverage'), false); // additive only for time-scoped searches
  assert.equal(calls, 2);
});

test('coverage preflight is authenticated and does zero lexical shard requests', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', time_min: 100, time_max: 200, url: 'https://a.test/search' },
    { shard_id: 'deepseek-0001', provider: 'deepseek', generation_id: 'b'.repeat(64), state: 'READY', time_min: 300, time_max: 400, url: 'https://b.test/search' },
  ];
  let dispatched = 0;
  const handler = makeRouterHandlerImpl({ catalog, authToken: ROUTER_TOKEN, shardAuthToken: SHARD_TOKEN,
    fetchImpl: async () => { dispatched += 1; throw Error('No shard dispatch in preflight'); } });
  const data = { provider: 'chatgpt', from_time: 250, to_time: 290 };
  const send = async (token, body) => handler.fetch(new Request('https://router.test/coverage', {
    method: 'POST', headers: { 'authorization': 'Bearer ' + token, 'content-type': 'application/json' },
    body: JSON.stringify(body),
  }));
  const denied = await send('wrong-token', data);
  assert.equal(denied.status, 401);
  const response = await send(ROUTER_TOKEN, data);
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.status, 'OBSERVED');
  assert.match(result.request_ref, /^[0-9a-f]{32}$/);
  assert.ok(!Number.isNaN(Date.parse(result.observed_at)));
  assert.equal(result.coverage.window_fit, 'OUTSIDE_OBSERVED_INDEX_RANGE');
  assert.equal(result.coverage.source_currentness, 'UNKNOWN_WITHOUT_SOURCE_WATERMARK');
  assert.equal(result.coverage.catalog_observed_max_time, 200);
  assert.deepEqual(result.catalog_generations, [{
    shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64),
    state: 'READY', time_min: 100, time_max: 200,
  }]);
  assert.equal(JSON.stringify(result).includes('https://'), false);
  assert.equal(dispatched, 0);
  const rejected = await send(ROUTER_TOKEN, { ...data, admin: true });
  assert.equal(rejected.status, 400);
  assert.equal((await rejected.json()).error, 'COVERAGE_FIELDS_INVALID');
  assert.equal((await send(ROUTER_TOKEN, { provider: 'chatgpt' })).status, 400);
});

test('coverage preflight caps provenance rows but evaluates the full catalog', async () => {
  const catalog = Array.from({ length: 34 }, (_, index) => ({
    shard_id: `chatgpt-${String(index).padStart(4, '0')}`,
    provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY',
    time_min: 100, time_max: 200, url: `https://shard-${index}.test/search`,
  }));
  const handler = makeRouterHandler({ catalog, fetchImpl: async () => { throw Error('unreachable'); } });
  const response = await handler.fetch(new Request('https://router.test/coverage', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ provider: 'chatgpt', from_time: 300 }),
  }));
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.coverage.eligible_shard_count, 34);
  assert.equal(result.coverage.window_fit, 'OUTSIDE_OBSERVED_INDEX_RANGE');
  assert.equal(result.catalog_generations.length, 32);
  assert.equal(result.catalog_generations_truncated, true);
  assert.equal(result.catalog_generations[0].shard_id, 'chatgpt-0000');
  assert.equal(result.catalog_generations.at(-1).shard_id, 'chatgpt-0031');
});

test('MCP coverage tool is discoverable and read-only with clean unknown-provider behavior', async () => {
  const catalog = [{ shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64),
    state: 'READY', time_min: 100, time_max: 200, url: 'https://a.test/search' }];
  let dispatched = 0;
  const handler = makeRouterHandler({ catalog, fetchImpl: async () => { dispatched += 1; throw Error('unexpected'); } });
  const invoke = async (method, params) => handler.fetch(new Request('https://router.test/mcp', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 47, method, params }),
  }));
  const tools = (await (await invoke('tools/list')).json()).result.tools;
  const coverageTool = tools.find((tool) => tool.name === 'session_coverage');
  assert.ok(coverageTool, 'session_coverage must be a standalone discoverable tool');
  assert.deepEqual(coverageTool.inputSchema.required, []);
  const reply = (await (await invoke('tools/call', { name: 'session_coverage',
    arguments: { provider: 'chatgpt', from_time: 250, to_time: 270 } })).json()).result;
  assert.equal(reply.isError, false);
  assert.equal(reply.structuredContent.coverage.window_fit, 'OUTSIDE_OBSERVED_INDEX_RANGE');
  const unknown = (await (await invoke('tools/call', { name: 'session_coverage',
    arguments: { provider: 'claude', from_time: 250 } })).json()).result;
  assert.equal(unknown.isError, false);
  assert.equal(unknown.structuredContent.coverage.window_fit, 'UNKNOWN_COVERAGE');
  assert.equal(unknown.structuredContent.coverage.eligible_shard_count, 0);
  const invalid = (await (await invoke('tools/call', { name: 'session_coverage',
    arguments: { provider: 'chatgpt', from_time: 250, to_time: 200 } })).json()).result;
  assert.equal(invalid.isError, true);
  assert.equal(dispatched, 0);
});

test('grouped router canary is disabled by default', async () => {
  const handler = makeRouterHandler({ catalog: [] });
  const response = await handler.fetch(new Request('https://router.test/internal/grouped-search', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'needle', search_mode: 'recall' }),
  }));
  assert.equal(response.status, 404);
  assert.deepEqual(await response.json(), { error: 'NOT_FOUND' });
});

test('enabled grouped router canary fans out grouped shard requests and merges exact text', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
    { shard_id: 'hermes-0001', provider: 'hermes', generation_id: 'b'.repeat(64), state: 'READY', url: 'https://b.test/search' },
  ];
  const seen = [];
  const fakeFetch = async (url, init) => {
    const request = JSON.parse(init.body);
    seen.push(request);
    assert.equal(request.result_shape, 'grouped_v1');
    assert.equal(request.candidate_budget, 100);
    assert.equal(request.search_mode, 'recall');
    const chatgpt = String(url).includes('a.test');
    const shardId = chatgpt ? 'chatgpt-0001' : 'hermes-0001';
    const provider = chatgpt ? 'chatgpt' : 'hermes';
    const generation = chatgpt ? 'a'.repeat(64) : 'b'.repeat(64);
    const prefix = chatgpt ? 'c' : 'h';
    return Response.json({
      generation_binding: 'router_pinned_v1',
      status: 'FOUND',
      shard_id: shardId,
      provider,
      generation_id: generation,
      grouping: 'exact_text_v1',
      groups: [
        {
          text: 'shared',
          representative: { session_id: `${prefix}a`, message_id: `${prefix}1`, ordinal: 1 },
          occurrence_count: 1,
          occurrences: [{ session_id: `${prefix}a`, message_id: `${prefix}1`, provider, shard_id: shardId, generation_id: generation }],
        },
        {
          text: chatgpt ? 'chat only' : 'hermes only',
          representative: { session_id: `${prefix}b`, message_id: `${prefix}2`, ordinal: 2 },
          occurrence_count: 1,
          occurrences: [{ session_id: `${prefix}b`, message_id: `${prefix}2`, provider, shard_id: shardId, generation_id: generation }],
        },
      ],
    });
  };
  const handler = makeRouterHandler({ catalog, fetchImpl: fakeFetch, groupedCanaryEnabled: true });
  const response = await handler.fetch(new Request('https://router.test/internal/grouped-search', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      query: 'needle',
      search_mode: 'recall',
      limit: 2,
      candidate_budget: 100,
    }),
  }));
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.status, 'FOUND');
  assert.equal(result.grouping, 'exact_text_v1');
  assert.equal(result.groups[0].text, 'shared');
  assert.equal(result.groups[0].occurrence_count, 2);
  assert.equal(result.groups[1].text, 'chat only');
  assert.equal(result.retrieval.candidate_budget, 100);
  assert.equal(result.retrieval.candidate_group_count, 4);
  assert.equal(result.retrieval.returned_group_count, 2);
  assert.match(result.request_ref, /^[0-9a-f]{32}$/);
  assert.equal(seen.length, 2);
});

test('router MCP tool returns the grouped default result', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
  ];
  const fakeFetch = async () => Response.json(groupedPayload({
    generationId: 'a'.repeat(64),
    shardId: 'chatgpt-0001',
    provider: 'chatgpt',
    text: 'shared',
    sessionId: 'ca',
    messageId: 'c1',
  }));
  const handler = makeRouterHandler({ catalog, fetchImpl: fakeFetch });
  const response = await handler.fetch(new Request('https://router.test/mcp', {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream' },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 9,
      method: 'tools/call',
      params: { name: 'session_search', arguments: { query: 'copper compass' } },
    }),
  }));
  const message = await response.json();
  const result = message.result.structuredContent;
  assert.equal(result.status, 'FOUND');
  assert.equal(result.grouping, 'exact_text_v1');
  assert.match(result.request_ref, /^[0-9a-f]{32}$/);
  assert.equal(result.groups[0].representative.shard_id, 'chatgpt-0001');
});


test('router times out a stalled shard and preserves healthy groups as PARTIAL', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
    { shard_id: 'hermes-0001', provider: 'hermes', generation_id: 'b'.repeat(64), state: 'READY', url: 'https://b.test/search' },
  ];
  const fakeFetch = async (url) => {
    if (String(url).includes('a.test')) {
      return Response.json(groupedPayload({
        generationId: 'a'.repeat(64), shardId: 'chatgpt-0001', provider: 'chatgpt',
        text: 'healthy', sessionId: 'ca', messageId: 'c1',
      }));
    }
    return {
      ok: true,
      async json() {
        return await new Promise(() => {});
      },
    };
  };
  const handler = makeRouterHandler({ catalog, fetchImpl: fakeFetch, shardTimeoutMs: 20 });
  const response = await handler.fetch(new Request('https://router.test/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'needle', limit: 8 }),
  }));
  const result = await response.json();
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(result.groups.map((x) => x.representative.shard_id), ['chatgpt-0001']);
  assert.deepEqual(result.unavailable_shards, [{ shard_id: 'hermes-0001', reason: 'TIMEOUT' }]);
});


test('router auto mode escalates grouped NO_SIGNAL to grouped recall with receipt', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
  ];
  const seenModes = [];
  const fakeFetch = async (_url, init) => {
    const request = JSON.parse(init.body);
    seenModes.push(request.search_mode);
    if (request.search_mode === 'strict') {
      return Response.json(groupedPayload({ generationId: 'a'.repeat(64) }));
    }
    return Response.json(groupedPayload({
      generationId: 'a'.repeat(64), shardId: 'chatgpt-0001', provider: 'chatgpt',
      text: 'recalled', sessionId: 'ca', messageId: 'c1',
    }));
  };
  const handler = makeRouterHandler({ catalog, fetchImpl: fakeFetch });
  const response = await handler.fetch(new Request('https://router.test/search', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'architecture plugin CLI completeness', search_mode: 'auto', limit: 8 }),
  }));
  const result = await response.json();
  assert.equal(result.status, 'FOUND');
  assert.deepEqual(seenModes, ['strict', 'recall']);
  assert.deepEqual(result.retrieval, {
    requested_mode: 'auto',
    effective_mode: 'recall',
    strict_group_count: 0,
    candidate_count: 1,
    candidate_group_count: 1,
    returned_group_count: 1,
  });
});


test('router auto mode does not hide PARTIAL by escalating', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
    { shard_id: 'chatgpt-0002', provider: 'chatgpt', generation_id: 'b'.repeat(64), state: 'READY', url: 'https://b.test/search' },
  ];
  const seenModes = [];
  const fakeFetch = async (url, init) => {
    const request = JSON.parse(init.body);
    seenModes.push(request.search_mode);
    if (String(url).includes('b.test')) return new Response('bad', { status: 503 });
    return Response.json(groupedPayload({ generationId: 'a'.repeat(64) }));
  };
  const handler = makeRouterHandler({ catalog, fetchImpl: fakeFetch });
  const response = await handler.fetch(new Request('https://router.test/search', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'needle', search_mode: 'auto' }),
  }));
  const result = await response.json();
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(seenModes, ['strict', 'strict']);
  assert.equal(result.retrieval.effective_mode, 'strict');
});
