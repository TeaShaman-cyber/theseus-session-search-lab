import test from 'node:test';
import assert from 'node:assert/strict';

import { makeRouterHandler as makeRouterHandlerImpl } from '../router/handler.mjs';
import { createSemanticEventsRuntime } from '../posthog-events.mjs';

const ROUTER_TOKEN = 'router-test-token';
const SHARD_TOKEN = 'shard-test-token';

function semanticRecorder() {
  const calls = [];
  return {
    calls,
    async capture(event, payload) { calls.push({ event, ...payload }); return true; },
  };
}

function makeRouterHandler({ semanticEvents, feedbackWriter = null, partial = false } = {}) {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' },
    ...(partial ? [{ shard_id: 'hermes-0001', provider: 'hermes', generation_id: 'b'.repeat(64), state: 'READY', url: 'https://b.test/search' }] : []),
  ];
  const handler = makeRouterHandlerImpl({
    catalog,
    authToken: ROUTER_TOKEN,
    shardAuthToken: SHARD_TOKEN,
    feedbackWriter,
    semanticEvents,
    shardTimeoutMs: partial ? 20 : 5000,
    fetchImpl: async (url) => {
      if (partial && String(url).includes('b.test')) return new Response('bad', { status: 503 });
      return Response.json({
        generation_binding: 'router_pinned_v1',
        status: 'FOUND',
        generation_id: 'a'.repeat(64),
        grouping: 'exact_text_v1',
        candidate_count: 1,
        returned_group_count: 1,
        groups: [{
          text: 'needle',
          representative: { session_id: 's', message_id: 'm', ordinal: 1 },
          occurrence_count: 1,
          occurrences: [{ session_id: 's', message_id: 'm', ordinal: 1 }],
        }],
      });
    },
  });
  return {
    async fetch(request) {
      const headers = new Headers(request.headers);
      headers.set('authorization', 'Bearer ' + ROUTER_TOKEN);
      return handler.fetch(new Request(request, { headers }));
    },
  };
}

test('direct search emits one completed semantic event', async () => {
  const semanticEvents = semanticRecorder();
  const handler = makeRouterHandler({ semanticEvents });
  const response = await handler.fetch(new Request('https://router.test/search', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'needle' }),
  }));
  const result = await response.json();
  assert.equal(result.status, 'FOUND');
  assert.deepEqual(semanticEvents.calls, [{
    event: 'session search completed',
    requestRef: result.request_ref,
    properties: { route: 'search', result_status: 'FOUND', queried_shards_count: 1, unavailable_shards_count: 0 },
  }]);
});

test('partial direct search emits the dedicated partial event', async () => {
  const semanticEvents = semanticRecorder();
  const handler = makeRouterHandler({ semanticEvents, partial: true });
  const response = await handler.fetch(new Request('https://router.test/search', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'needle' }),
  }));
  const result = await response.json();
  assert.equal(result.status, 'PARTIAL');
  assert.equal(semanticEvents.calls[0].event, 'session search partial');
  assert.deepEqual(semanticEvents.calls[0].properties, {
    route: 'search', result_status: 'PARTIAL', queried_shards_count: 2, unavailable_shards_count: 1,
  });
});

test('MCP session_search emits the same search semantics with route=mcp', async () => {
  const semanticEvents = semanticRecorder();
  const handler = makeRouterHandler({ semanticEvents });
  const response = await handler.fetch(new Request('https://router.test/mcp', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'session_search', arguments: { query: 'needle' } } }),
  }));
  const message = await response.json();
  assert.equal(message.result.structuredContent.status, 'FOUND');
  assert.deepEqual(semanticEvents.calls, [{
    event: 'session search completed',
    requestRef: message.result.structuredContent.request_ref,
    properties: { route: 'mcp', result_status: 'FOUND', queried_shards_count: 1, unavailable_shards_count: 0 },
  }]);
});

test('MCP session_feedback emits only storage outcome semantics', async () => {
  const semanticEvents = semanticRecorder();
  const handler = makeRouterHandler({ semanticEvents, feedbackWriter: async () => ({ status: 'RECORDED', feedback_ref: 'b'.repeat(32) }) });
  const response = await handler.fetch(new Request('https://router.test/mcp', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0', id: 2, method: 'tools/call',
      params: { name: 'session_feedback', arguments: {
        baseline_request_ref: 'c'.repeat(32), failure_class: 'ranking',
        operator: { name: 'test', version: 'v1' }, outcome: 'improved', notes: 'private note must not escape',
      } },
    }),
  }));
  const message = await response.json();
  assert.equal(message.result.structuredContent.status, 'RECORDED');
  assert.equal(semanticEvents.calls.length, 1);
  assert.equal(semanticEvents.calls[0].event, 'session feedback submitted');
  assert.deepEqual(semanticEvents.calls[0].properties, { route: 'mcp', result_status: 'RECORDED' });
  assert.equal(JSON.stringify(semanticEvents.calls).includes('private note'), false);
});

test('direct coverage emits one classification event without time bounds', async () => {
  const semanticEvents = semanticRecorder();
  const handler = makeRouterHandler({ semanticEvents });
  const response = await handler.fetch(new Request('https://router.test/coverage', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ provider: 'chatgpt', from_time: 250, to_time: 270 }),
  }));
  const result = await response.json();
  assert.equal(result.status, 'OBSERVED');
  assert.equal(semanticEvents.calls.length, 1);
  assert.deepEqual(semanticEvents.calls[0], {
    event: 'session coverage classified',
    requestRef: result.request_ref,
    properties: { route: 'coverage', result_status: result.coverage.window_fit },
  });
  assert.equal(JSON.stringify(semanticEvents.calls).includes('250'), false);
});

test('MCP session_coverage emits the same classification with route=mcp', async () => {
  const semanticEvents = semanticRecorder();
  const handler = makeRouterHandler({ semanticEvents });
  const response = await handler.fetch(new Request('https://router.test/mcp', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0', id: 3, method: 'tools/call',
      params: { name: 'session_coverage', arguments: { provider: 'chatgpt', from_time: 250, to_time: 270 } },
    }),
  }));
  const message = await response.json();
  assert.equal(message.result.structuredContent.status, 'OBSERVED');
  assert.deepEqual(semanticEvents.calls, [{
    event: 'session coverage classified',
    requestRef: message.result.structuredContent.request_ref,
    properties: { route: 'mcp', result_status: message.result.structuredContent.coverage.window_fit },
  }]);
});


test('PostHog capture failure cannot change a successful search response', async () => {
  const semanticEvents = createSemanticEventsRuntime({
    client: {
      capture() { throw new Error('private SDK failure'); },
      async flush() {},
      on() {},
    },
    logger: () => {},
  });
  const handler = makeRouterHandler({ semanticEvents });
  const response = await handler.fetch(new Request('https://router.test/search', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'needle' }),
  }));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).status, 'FOUND');
});
