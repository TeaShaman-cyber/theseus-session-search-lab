import test from 'node:test';
import assert from 'node:assert/strict';

import { buildGroupedSearchCall, buildSearchCall } from '../shard/core.mjs';
import { mergeShardOutcomes, selectShards, handleMcpRequest, validateSearchInput } from '../router/core.mjs';

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
  assert.deepEqual(call.values, ['copper compass', ['dialogue'], 8, 'session-a', 1000, 2000, 'strict']);
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

test('MCP exposes session_feedback only when a writer is configured', async () => {
  const withoutWriter = await handleMcpRequest(
    { jsonrpc: '2.0', id: 31, method: 'tools/list', params: {} },
    async () => ({}),
  );
  assert.deepEqual(withoutWriter.result.tools.map((tool) => tool.name), ['session_search']);

  const withWriter = await handleMcpRequest(
    { jsonrpc: '2.0', id: 32, method: 'tools/list', params: {} },
    async () => ({}),
    async () => ({ status: 'RECORDED', feedback_ref: 'f'.repeat(32) }),
  );
  assert.deepEqual(
    withWriter.result.tools.map((tool) => tool.name),
    ['session_search', 'session_feedback'],
  );
  const schema = withWriter.result.tools[1].inputSchema;
  assert.equal(schema.additionalProperties, false);
  assert.deepEqual(schema.required, [
    'baseline_request_ref',
    'failure_class',
    'operator',
    'outcome',
  ]);
  assert.equal(schema.properties.operator.additionalProperties, false);
});

test('session_feedback validates exact bounded input before invoking writer', async () => {
  const seen = [];
  const feedback = {
    baseline_request_ref: 'a'.repeat(32),
    result_request_ref: 'b'.repeat(32),
    failure_class: 'noisy_query',
    operator: { name: 'light_sonar_rephrase', version: 'v1' },
    outcome: 'improved',
    notes: 'Recovered a narrower relevant result set.',
  };
  const called = await handleMcpRequest(
    {
      jsonrpc: '2.0',
      id: 33,
      method: 'tools/call',
      params: { name: 'session_feedback', arguments: feedback },
    },
    async () => ({}),
    async (input) => {
      seen.push(input);
      return { status: 'RECORDED', feedback_ref: 'c'.repeat(32) };
    },
  );
  assert.deepEqual(seen, [feedback]);
  assert.deepEqual(called.result.structuredContent, {
    status: 'RECORDED',
    feedback_ref: 'c'.repeat(32),
  });
  assert.equal(called.result.isError, false);
});

test('session_feedback fails closed on unknown or invalid fields', async () => {
  let calls = 0;
  const writer = async () => {
    calls += 1;
    return { status: 'RECORDED' };
  };
  const bad = await handleMcpRequest(
    {
      jsonrpc: '2.0',
      id: 34,
      method: 'tools/call',
      params: {
        name: 'session_feedback',
        arguments: {
          baseline_request_ref: 'a'.repeat(32),
          failure_class: 'noisy_query',
          operator: { name: 'light_sonar_rephrase', version: 'v1' },
          outcome: 'improved',
          query_text: 'must never enter feedback',
        },
      },
    },
    async () => ({}),
    writer,
  );
  assert.equal(bad.result.isError, true);
  assert.match(bad.result.content[0].text, /FEEDBACK_FIELDS_INVALID/);
  assert.equal(calls, 0);
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


test('router rejects invalid routing filters before shard selection', () => {
  assert.throws(
    () => validateSearchInput({ query: 'needle', session_id: 42 }),
    /SESSION_OUT_OF_BOUNDS/,
  );
  assert.throws(
    () => validateSearchInput({ query: 'needle', from_time: Number.NaN }),
    /TIME_OUT_OF_BOUNDS/,
  );
  assert.throws(
    () => validateSearchInput({ query: 'needle', from_time: 70, to_time: 20 }),
    /TIME_BOUNDS_INVERTED/,
  );
});


test('router retrieval policy defaults to strict and validates explicit modes', () => {
  assert.equal(validateSearchInput({ query: 'needle' }).search_mode, 'strict');
  assert.equal(validateSearchInput({ query: 'needle', search_mode: 'recall' }).search_mode, 'recall');
  assert.equal(validateSearchInput({ query: 'needle', search_mode: 'auto' }).search_mode, 'auto');
  assert.throws(
    () => validateSearchInput({ query: 'needle', search_mode: 'semantic' }),
    /SEARCH_MODE_OUT_OF_BOUNDS/,
  );
});

test('public router contract rejects internal grouped-canary controls', () => {
  assert.throws(
    () => validateSearchInput({ query: 'needle', grouping: 'exact_text_v1' }),
    /GROUPED_SEARCH_NOT_PUBLIC/,
  );
  assert.throws(
    () => validateSearchInput({ query: 'needle', candidate_budget: 24 }),
    /GROUPED_SEARCH_NOT_PUBLIC/,
  );
});

test('MCP advertises strict recall auto retrieval policy', async () => {
  const listed = await handleMcpRequest({ jsonrpc: '2.0', id: 21, method: 'tools/list', params: {} }, async () => ({}));
  const schema = listed.result.tools[0].inputSchema.properties.search_mode;
  assert.deepEqual(schema.enum, ['strict', 'recall', 'auto']);
  assert.equal(schema.default, 'strict');
  const properties = listed.result.tools[0].inputSchema.properties;
  assert.equal(Object.hasOwn(properties, 'grouping'), false);
  assert.equal(Object.hasOwn(properties, 'candidate_budget'), false);
});

test('shard bounded SQL call carries strict or recall mode through versioned search_v2', () => {
  const strict = buildSearchCall({ query: 'copper compass', search_mode: 'strict' });
  assert.match(strict.text, /session_search\.search_v2/);
  assert.equal(strict.values.at(-1), 'strict');

  const recall = buildSearchCall({ query: 'copper compass', search_mode: 'recall' });
  assert.equal(recall.values.at(-1), 'recall');
  assert.throws(
    () => buildSearchCall({ query: 'copper compass', search_mode: 'auto' }),
    /SEARCH_MODE_OUT_OF_BOUNDS/,
  );
});


test('opt-in grouped shard call separates candidate budget from result limit without changing legacy call', () => {
  const generation = 'c'.repeat(64);
  const grouped = buildGroupedSearchCall({
    generation_id: generation,
    query: 'needle',
    scopes: ['dialogue', 'evidence'],
    limit: 10,
    candidate_budget: 25,
    search_mode: 'recall',
  });
  assert.equal(
    grouped.text,
    'SELECT session_search.search_generation_grouped_v2($1,$2,$3::text[],$4,$5,$6,$7,$8,$9) AS result',
  );
  assert.deepEqual(grouped.values, [
    generation, 'needle', ['dialogue', 'evidence'], 10, 25, null, null, null, 'recall',
  ]);

  const parityDefault = buildGroupedSearchCall({
    generation_id: generation,
    query: 'needle',
    limit: 10,
    search_mode: 'recall',
  });
  assert.equal(parityDefault.values[4], 30);

  const cappedDefault = buildGroupedSearchCall({
    generation_id: generation,
    query: 'needle',
    limit: 100,
    search_mode: 'recall',
  });
  assert.equal(cappedDefault.values[4], 300);

  const legacy = buildSearchCall({
    generation_id: generation,
    query: 'needle',
    limit: 10,
    search_mode: 'recall',
  });
  assert.match(legacy.text, /search_generation_v2/);
  assert.equal(legacy.values.length, 8);
  assert.throws(
    () => buildGroupedSearchCall({ query: 'needle', search_mode: 'recall' }),
    /GENERATION_REQUIRED_FOR_GROUPED_SEARCH/,
  );
  assert.throws(
    () => buildGroupedSearchCall({ generation_id: generation, query: 'needle', search_mode: 'strict' }),
    /GROUPED_SEARCH_RECALL_ONLY/,
  );
  for (const candidateBudget of [9, 301, 1.5, '30']) {
    assert.throws(
      () => buildGroupedSearchCall({
        generation_id: generation,
        query: 'needle',
        limit: 10,
        candidate_budget: candidateBudget,
        search_mode: 'recall',
      }),
      /CANDIDATE_BUDGET_OUT_OF_BOUNDS/,
    );
  }
});
