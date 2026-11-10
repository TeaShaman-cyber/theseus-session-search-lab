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


test('shard HTTP adapter executes one bounded query and returns its result', async () => {
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
  assert.deepEqual((await response.json()).hits.map((x) => x.message_id), ['c1']);
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

test('router HTTP adapter fans out and merges the same search path', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
    { shard_id: 'hermes-0001', provider: 'hermes', generation_id: 'b'.repeat(64), state: 'READY', url: 'https://b.test/search' },
  ];
  const seen = [];
  const fakeFetch = async (url, init) => {
    seen.push(String(url));
    const request = JSON.parse(init.body);
    assert.equal(request.query, 'copper compass');
    const a = String(url).includes('a.test');
    return Response.json({ generation_binding: 'router_pinned_v1',
      status: 'FOUND',
      generation_id: a ? 'a'.repeat(64) : 'b'.repeat(64),
      hits: [{ session_id: a ? 'ca' : 'hb', message_id: a ? 'c1' : 'h1', ordinal: 1, text: 'shared' }],
    });
  };
  const handler = makeRouterHandler({ catalog, fetchImpl: fakeFetch });
  const response = await handler.fetch(new Request('https://router.test/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'copper compass', limit: 8 }),
  }));
  const result = await response.json();
  assert.equal(result.status, 'FOUND');
  assert.match(result.request_ref, /^[0-9a-f]{32}$/);
  assert.deepEqual(seen.sort(), ['https://a.test/search', 'https://b.test/search']);
  assert.deepEqual(result.hits.map((x) => x.shard_id), ['chatgpt-0001', 'hermes-0001']);
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
    assert.equal(request.grouping, 'exact_text_v1');
    assert.equal(request.candidate_budget, 6);
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
      candidate_budget: 6,
    }),
  }));
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.status, 'FOUND');
  assert.equal(result.grouping, 'exact_text_v1');
  assert.equal(result.groups[0].text, 'shared');
  assert.equal(result.groups[0].occurrence_count, 2);
  assert.equal(result.groups[1].text, 'chat only');
  assert.equal(result.retrieval.candidate_budget, 6);
  assert.equal(result.retrieval.candidate_group_count, 4);
  assert.equal(result.retrieval.returned_group_count, 2);
  assert.match(result.request_ref, /^[0-9a-f]{32}$/);
  assert.equal(seen.length, 2);
});

test('router MCP tool call reuses federated search', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
  ];
  const fakeFetch = async () => Response.json({ generation_binding: 'router_pinned_v1',
    status: 'FOUND',
    generation_id: 'a'.repeat(64),
    hits: [{ session_id: 'ca', message_id: 'c1', ordinal: 1, text: 'shared' }],
  });
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
  assert.equal(message.result.structuredContent.status, 'FOUND');
  assert.match(message.result.structuredContent.request_ref, /^[0-9a-f]{32}$/);
  assert.equal(message.result.structuredContent.hits[0].shard_id, 'chatgpt-0001');
});


test('router times out a stalled shard and preserves healthy hits as PARTIAL', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
    { shard_id: 'hermes-0001', provider: 'hermes', generation_id: 'b'.repeat(64), state: 'READY', url: 'https://b.test/search' },
  ];
  const fakeFetch = async (url) => {
    if (String(url).includes('a.test')) {
      return Response.json({ generation_binding: 'router_pinned_v1',
        status: 'FOUND', generation_id: 'a'.repeat(64),
        hits: [{ session_id: 'ca', message_id: 'c1', ordinal: 1, text: 'healthy' }],
      });
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
  assert.deepEqual(result.hits.map((x) => x.shard_id), ['chatgpt-0001']);
  assert.deepEqual(result.unavailable_shards, [{ shard_id: 'hermes-0001', reason: 'TIMEOUT' }]);
});


test('router auto mode escalates NO_SIGNAL strict search to recall and returns retrieval receipt', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
  ];
  const seenModes = [];
  const fakeFetch = async (_url, init) => {
    const request = JSON.parse(init.body);
    seenModes.push(request.search_mode);
    if (request.search_mode === 'strict') {
      return Response.json({ generation_binding: 'router_pinned_v1', status: 'NO_SIGNAL', generation_id: 'a'.repeat(64), hits: [] });
    }
    return Response.json({ generation_binding: 'router_pinned_v1',
      status: 'FOUND', generation_id: 'a'.repeat(64),
      hits: [{ session_id: 'ca', message_id: 'c1', ordinal: 1, text: 'recalled' }],
    });
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
    strict_hit_count: 0,
    candidate_count: 1,
    returned_hit_count: 1,
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
    return Response.json({ generation_binding: 'router_pinned_v1', status: 'NO_SIGNAL', generation_id: 'a'.repeat(64), hits: [] });
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
