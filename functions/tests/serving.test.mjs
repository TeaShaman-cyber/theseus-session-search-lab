import test from 'node:test';
import assert from 'node:assert/strict';

import { buildSearchCall } from '../shard/core.mjs';
import { mergeShardOutcomes, selectShards, handleMcpRequest } from '../router/core.mjs';

test('shard request becomes one bounded SQL function call', () => {
  const call = buildSearchCall({
    query: 'copper compass',
    scopes: ['dialogue'],
    limit: 8,
    session_id: 'session-a',
    from_time: 1000,
    to_time: 2000,
  });
  assert.match(call.text, /session_search\.search/);
  assert.deepEqual(call.values, ['copper compass', ['dialogue'], 8, 'session-a', 1000, 2000]);
});

test('router scopes by provider before fanout', () => {
  const catalog = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), url: 'https://a.invalid/search' },
    { shard_id: 'hermes-0001', provider: 'hermes', generation_id: 'b'.repeat(64), url: 'https://b.invalid/search' },
  ];
  assert.deepEqual(selectShards(catalog, { provider: 'hermes' }).map((x) => x.shard_id), ['hermes-0001']);
});

test('router merges ready outcomes deterministically with provenance', () => {
  const selected = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64) },
    { shard_id: 'hermes-0001', provider: 'hermes', generation_id: 'b'.repeat(64) },
  ];
  const outcomes = [
    { shard: selected[0], ok: true, body: { generation_id: 'a'.repeat(64), hits: [{ session_id: 'c', message_id: 'c1', ordinal: 1, text: 'x' }] } },
    { shard: selected[1], ok: true, body: { generation_id: 'b'.repeat(64), hits: [{ session_id: 'h', message_id: 'h1', ordinal: 1, text: 'x' }] } },
  ];
  const result = mergeShardOutcomes(selected, outcomes, 8);
  assert.equal(result.status, 'FOUND');
  assert.deepEqual(result.queried_shards, ['chatgpt-0001', 'hermes-0001']);
  assert.deepEqual(result.hits.map((h) => [h.shard_id, h.generation_id]), [
    ['chatgpt-0001', 'a'.repeat(64)],
    ['hermes-0001', 'b'.repeat(64)],
  ]);
});

test('router reports PARTIAL when one shard fails', () => {
  const selected = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64) },
    { shard_id: 'chatgpt-0002', provider: 'chatgpt', generation_id: 'b'.repeat(64) },
  ];
  const result = mergeShardOutcomes(selected, [
    { shard: selected[0], ok: true, body: { generation_id: 'a'.repeat(64), hits: [{ session_id: 's', message_id: 'm', ordinal: 1, text: 'ok' }] } },
    { shard: selected[1], ok: false, reason: 'QUERY_FAILED' },
  ], 8);
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(result.unavailable_shards, [{ shard_id: 'chatgpt-0002', reason: 'QUERY_FAILED' }]);
});

test('MCP exposes one bounded session_search tool', async () => {
  const initialize = await handleMcpRequest({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2025-06-18' } }, async () => ({}));
  assert.equal(initialize.result.serverInfo.name, 'theseus-session-search');

  const listed = await handleMcpRequest({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} }, async () => ({}));
  assert.deepEqual(listed.result.tools.map((t) => t.name), ['session_search']);

  const called = await handleMcpRequest({
    jsonrpc: '2.0', id: 3, method: 'tools/call',
    params: { name: 'session_search', arguments: { query: 'copper compass', limit: 8 } },
  }, async () => ({ status: 'FOUND', hits: [] }));
  assert.equal(called.result.structuredContent.status, 'FOUND');
});
