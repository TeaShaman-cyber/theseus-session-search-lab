import test from 'node:test';
import assert from 'node:assert/strict';

import { buildGroupedSearchCall, buildSearchCall } from '../shard/core.mjs';
import {
  handleMcpRequest,
  mergeGroupedShardOutcomes,
  mergeShardOutcomes,
  selectShards,
  validateGroupedCanaryInput,
  validateSearchInput,
} from '../router/core.mjs';

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

test('router grouped merge collapses equal text across shards and preserves occurrences', () => {
  const selected = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64) },
    { shard_id: 'hermes-0001', provider: 'hermes', generation_id: 'b'.repeat(64) },
  ];
  const outcomes = [
    { shard: selected[0], ok: true, body: { generation_id: 'a'.repeat(64), grouping: 'exact_text_v1', groups: [
      { text: 'same', representative: { session_id: 'ca', message_id: 'c1', ordinal: 1 }, occurrence_count: 1, occurrences: [{ session_id: 'ca', message_id: 'c1', provider: 'chatgpt', shard_id: 'chatgpt-0001', generation_id: 'a'.repeat(64) }] },
      { text: 'chat only', representative: { session_id: 'ca', message_id: 'c2', ordinal: 2 }, occurrence_count: 1, occurrences: [{ session_id: 'ca', message_id: 'c2', provider: 'chatgpt', shard_id: 'chatgpt-0001', generation_id: 'a'.repeat(64) }] },
    ] } },
    { shard: selected[1], ok: true, body: { generation_id: 'b'.repeat(64), grouping: 'exact_text_v1', groups: [
      { text: 'same', representative: { session_id: 'hb', message_id: 'h1', ordinal: 1 }, occurrence_count: 1, occurrences: [{ session_id: 'hb', message_id: 'h1', provider: 'hermes', shard_id: 'hermes-0001', generation_id: 'b'.repeat(64) }] },
      { text: 'hermes only', representative: { session_id: 'hb', message_id: 'h2', ordinal: 2 }, occurrence_count: 1, occurrences: [{ session_id: 'hb', message_id: 'h2', provider: 'hermes', shard_id: 'hermes-0001', generation_id: 'b'.repeat(64) }] },
    ] } },
  ];

  const result = mergeGroupedShardOutcomes(selected, outcomes, 2);
  assert.equal(result.status, 'FOUND');
  assert.equal(result.grouping, 'exact_text_v1');
  assert.equal(result.candidate_group_count, 4);
  assert.equal(result.returned_group_count, 2);
  assert.deepEqual(result.groups.map((group) => group.text), ['same', 'chat only']);
  assert.equal(result.groups[0].occurrence_count, 2);
  assert.deepEqual(
    result.groups[0].occurrences.map((occurrence) => [occurrence.provider, occurrence.shard_id, occurrence.generation_id]),
    [
      ['chatgpt', 'chatgpt-0001', 'a'.repeat(64)],
      ['hermes', 'hermes-0001', 'b'.repeat(64)],
    ],
  );
});

test('router grouped merge preserves healthy groups and PARTIAL on shard failure', () => {
  const selected = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64) },
    { shard_id: 'chatgpt-0002', provider: 'chatgpt', generation_id: 'b'.repeat(64) },
  ];
  const result = mergeGroupedShardOutcomes(selected, [
    { shard: selected[0], ok: true, body: { generation_id: 'a'.repeat(64), grouping: 'exact_text_v1', groups: [
      { text: 'healthy', representative: { session_id: 's1' }, occurrence_count: 1, occurrences: [{ session_id: 's1' }] },
    ] } },
    { shard: selected[1], ok: false, reason: 'TIMEOUT' },
  ], 8);
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(result.groups.map((group) => group.text), ['healthy']);
  assert.deepEqual(result.unavailable_shards, [{ shard_id: 'chatgpt-0002', reason: 'TIMEOUT' }]);
});

test('router grouped merge isolates malformed shard group payload as PARTIAL', () => {
  const selected = [
    { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64) },
    { shard_id: 'chatgpt-0002', provider: 'chatgpt', generation_id: 'b'.repeat(64) },
  ];
  const result = mergeGroupedShardOutcomes(selected, [
    { shard: selected[0], ok: true, body: { generation_id: 'a'.repeat(64), grouping: 'exact_text_v1', groups: [
      { text: 'healthy', representative: { session_id: 's1' }, occurrence_count: 1, occurrences: [{ session_id: 's1' }] },
    ] } },
    { shard: selected[1], ok: true, body: { generation_id: 'b'.repeat(64), grouping: 'exact_text_v1', groups: [
      { text: 'broken', representative: { session_id: 's2' }, occurrence_count: 2, occurrences: [{ session_id: 's2' }] },
    ] } },
  ], 8);
  assert.equal(result.status, 'PARTIAL');
  assert.deepEqual(result.groups.map((group) => group.text), ['healthy']);
  assert.deepEqual(result.unavailable_shards, [{ shard_id: 'chatgpt-0002', reason: 'GROUP_PAYLOAD_INVALID' }]);
});

test('grouped canary validation is recall-only and keeps candidate budget separate', () => {
  const input = validateGroupedCanaryInput({
    query: 'needle',
    search_mode: 'recall',
    limit: 10,
    candidate_budget: 25,
  });
  assert.equal(input.limit, 10);
  assert.equal(input.candidate_budget, 25);
  assert.equal(
    validateGroupedCanaryInput({ query: 'needle', search_mode: 'recall', limit: 10 }).candidate_budget,
    30,
  );
  assert.throws(
    () => validateGroupedCanaryInput({ query: 'needle', search_mode: 'strict' }),
    /GROUPED_SEARCH_RECALL_ONLY/,
  );
  assert.throws(
    () => validateGroupedCanaryInput({ query: 'needle', search_mode: 'recall', grouping: 'exact_text_v1' }),
    /GROUPING_CONTROL_IMPLICIT/,
  );
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

test('public router contract rejects internal result-shape controls', () => {
  assert.throws(
    () => validateSearchInput({ query: 'needle', grouping: 'exact_text_v1' }),
    /INTERNAL_SEARCH_CONTROL_NOT_PUBLIC/,
  );
  assert.throws(
    () => validateSearchInput({ query: 'needle', candidate_budget: 24 }),
    /INTERNAL_SEARCH_CONTROL_NOT_PUBLIC/,
  );
  assert.throws(
    () => validateSearchInput({ query: 'needle', result_shape: 'flat_v1' }),
    /INTERNAL_SEARCH_CONTROL_NOT_PUBLIC/,
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


test('grouped shard call is the versioned default shape for strict and recall', () => {
  const generation = 'c'.repeat(64);
  const strict = buildGroupedSearchCall({
    generation_id: generation,
    query: 'needle',
    scopes: ['dialogue', 'evidence'],
    limit: 10,
    candidate_budget: 25,
    search_mode: 'strict',
  });
  assert.equal(
    strict.text,
    'SELECT session_search.search_generation_grouped_v3($1,$2,$3::text[],$4,$5,$6,$7,$8,$9) AS result',
  );
  assert.deepEqual(strict.values, [
    generation, 'needle', ['dialogue', 'evidence'], 10, 25, null, null, null, 'strict',
  ]);

  const recall = buildGroupedSearchCall({
    generation_id: generation,
    query: 'needle',
    limit: 10,
    search_mode: 'recall',
  });
  assert.equal(recall.values[4], 30);
  assert.equal(recall.values.at(-1), 'recall');

  const active = buildGroupedSearchCall({ query: 'needle', limit: 10, search_mode: 'strict' });
  assert.equal(
    active.text,
    'SELECT session_search.search_grouped_v3($1,$2::text[],$3,$4,$5,$6,$7,$8) AS result',
  );
  assert.equal(active.values[3], 30);

  const cappedDefault = buildGroupedSearchCall({
    generation_id: generation,
    query: 'needle',
    limit: 100,
    search_mode: 'recall',
  });
  assert.equal(cappedDefault.values[4], 300);

  const flatRollback = buildSearchCall({
    generation_id: generation,
    query: 'needle',
    limit: 10,
    search_mode: 'recall',
  });
  assert.match(flatRollback.text, /search_generation_v2/);
  assert.equal(flatRollback.values.length, 8);

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
