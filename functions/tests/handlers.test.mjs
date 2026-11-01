import test from 'node:test';
import assert from 'node:assert/strict';

import { makeShardHandler } from '../shard/handler.mjs';
import { makeRouterHandler } from '../router/handler.mjs';

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
    return Response.json({
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
  assert.deepEqual(seen.sort(), ['https://a.test/search', 'https://b.test/search']);
  assert.deepEqual(result.hits.map((x) => x.shard_id), ['chatgpt-0001', 'hermes-0001']);
});

test('router MCP tool call reuses federated search', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
  ];
  const fakeFetch = async () => Response.json({
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
  assert.equal(message.result.structuredContent.hits[0].shard_id, 'chatgpt-0001');
});


test('router times out a stalled shard and preserves healthy hits as PARTIAL', async () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
    { shard_id: 'hermes-0001', provider: 'hermes', generation_id: 'b'.repeat(64), state: 'READY', url: 'https://b.test/search' },
  ];
  const fakeFetch = async (url) => {
    if (String(url).includes('a.test')) {
      return Response.json({
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
      return Response.json({ status: 'NO_SIGNAL', generation_id: 'a'.repeat(64), hits: [] });
    }
    return Response.json({
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
    return Response.json({ status: 'NO_SIGNAL', generation_id: 'a'.repeat(64), hits: [] });
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
