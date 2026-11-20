import test from 'node:test';
import assert from 'node:assert/strict';

import { InMemoryMetricExporter, AggregationTemporality } from '@opentelemetry/sdk-metrics';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace';
import { createNoopTelemetry, createTelemetryRuntime } from '../telemetry.mjs';
import { makeRouterHandler as makeRouterHandlerImpl } from '../router/handler.mjs';
import { makeShardHandler as makeShardHandlerImpl } from '../shard/handler.mjs';


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


function runtime() {
  const spanExporter = new InMemorySpanExporter();
  const metricExporter = new InMemoryMetricExporter(AggregationTemporality.CUMULATIVE);
  let spanFlushes = 0;
  let metricFlushes = 0;
  const originalSpanFlush = spanExporter.forceFlush.bind(spanExporter);
  const originalMetricFlush = metricExporter.forceFlush.bind(metricExporter);
  spanExporter.forceFlush = async () => { spanFlushes += 1; return originalSpanFlush(); };
  metricExporter.forceFlush = async () => { metricFlushes += 1; return originalMetricFlush(); };
  const telemetry = createTelemetryRuntime({
    serviceName: 'session-search-router',
    serviceVersion: 'test',
    environment: 'candidate',
    traceExporter: spanExporter,
    metricExporter,
    metricExportIntervalMs: 60000,
  });
  return { telemetry, spanExporter, metricExporter, flushes: () => ({ spanFlushes, metricFlushes }) };
}

test('noop telemetry preserves handler behavior when OTLP is disabled', async () => {
  const telemetry = createNoopTelemetry();
  let requestRef;
  const response = await telemetry.withRequest(
    new Request('https://router.test/search?secret=must-not-matter'),
    'search',
    async (scope) => {
      requestRef = scope.requestRef;
      return Response.json({ status: 'FOUND' });
    },
  );
  assert.equal(response.status, 200);
  assert.match(requestRef, /^[0-9a-f]{32}$/);
  assert.equal((await response.json()).status, 'FOUND');
});

test('router propagates W3C trace context to shard and flushes serverless telemetry', async () => {
  const { telemetry, spanExporter, metricExporter, flushes } = runtime();
  try {
    let traceparent;
    const catalog = [{
      shard_id: 'deepseek-0001', provider: 'deepseek', generation_id: 'a'.repeat(64),
      state: 'READY', url: 'https://secret.example/search?token=never-export',
    }];
    const handler = makeRouterHandler({
      catalog,
      telemetry,
      fetchImpl: async (_url, init) => {
        traceparent = new Headers(init.headers).get('traceparent');
        return Response.json({
          generation_binding: 'router_pinned_v1',
          status: 'FOUND', generation_id: 'a'.repeat(64), provider: 'deepseek', shard_id: 'deepseek-0001', hits: [],
        });
      },
    });
    const response = await handler.fetch(new Request('https://router.test/search?q=super-secret-query', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ query: 'super-secret-query' }),
    }));
    assert.equal(response.status, 200);
    const responseBody = await response.json();
    assert.match(responseBody.request_ref, /^[0-9a-f]{32}$/);
    assert.match(traceparent ?? '', /^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$/);

    const spans = spanExporter.getFinishedSpans();
    assert.equal(spans.length, 2);
    const server = spans.find((span) => span.name === 'session_search.search');
    const dispatch = spans.find((span) => span.name === 'session_search.shard_dispatch');
    assert.ok(server);
    assert.ok(dispatch);
    assert.equal(server.spanContext().traceId, dispatch.spanContext().traceId);
    assert.equal(dispatch.parentSpanContext?.spanId, server.spanContext().spanId);
    assert.equal(server.attributes['session_search.request_ref'], responseBody.request_ref);

    const encoded = JSON.stringify(spans.map((span) => ({ name: span.name, attributes: span.attributes })));
    assert.equal(encoded.includes('super-secret-query'), false);
    assert.equal(encoded.includes('secret.example'), false);
    assert.equal(encoded.includes('token=never-export'), false);
    assert.equal(encoded.includes('https://'), false);
    assert.match(encoded, /deepseek-0001/);
    assert.match(encoded, new RegExp('a'.repeat(64)));

    const metricPayload = JSON.stringify(metricExporter.getMetrics());
    assert.equal(metricPayload.includes('super-secret-query'), false);
    assert.equal(metricPayload.includes('secret.example'), false);
    assert.equal(metricPayload.includes('token=never-export'), false);
    assert.equal(metricPayload.includes('https://'), false);
    assert.equal(metricPayload.includes('a'.repeat(64)), false);
    assert.equal(metricPayload.includes(responseBody.request_ref), false);
    assert.match(metricPayload, /deepseek-0001/);

    const counts = flushes();
    assert.ok(counts.spanFlushes >= 1);
    assert.ok(counts.metricFlushes >= 1);
  } finally {
    await telemetry.shutdown();
  }
});


test('grouped canary request keeps its bounded telemetry route name', async () => {
  const { telemetry, spanExporter } = runtime();
  try {
    const handler = makeRouterHandler({
      catalog: [],
      telemetry,
      groupedCanaryEnabled: true,
      fetchImpl: async () => { throw new Error('empty catalog must not dispatch'); },
    });
    const response = await handler.fetch(new Request('https://router.test/internal/grouped-search', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'canary', search_mode: 'recall', limit: 2, candidate_budget: 6 }),
    }));
    assert.equal(response.status, 200);
    const server = spanExporter.getFinishedSpans().find(
      (span) => span.name === 'session_search.grouped_search_canary',
    );
    assert.ok(server);
    assert.equal(server.attributes['session_search.route'], 'grouped_search_canary');
    assert.notEqual(server.attributes['session_search.route'], 'not_found');
  } finally {
    await telemetry.shutdown();
  }
});

test('request spans expose bounded status and error class for TraceQL window metrics', async () => {
  const { telemetry, spanExporter } = runtime();
  try {
    const handler = makeRouterHandler({
      catalog: [{
        shard_id: 'deepseek-0001', provider: 'deepseek', generation_id: 'd'.repeat(64),
        state: 'READY', url: 'https://deepseek.test/search',
      }],
      telemetry,
      fetchImpl: async () => Response.json({ status: 'NO_SIGNAL', generation_id: 'd'.repeat(64), generation_binding: 'router_pinned_v1', hits: [] }),
    });
    const response = await handler.fetch(new Request('https://router.test/search', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({}),
    }));
    assert.equal(response.status, 400);

    const server = spanExporter.getFinishedSpans().find((span) => span.name === 'session_search.search');
    assert.ok(server);
    assert.equal(server.attributes['session_search.status_class'], '4xx');
    assert.equal(server.attributes['session_search.error_class'], 'validation');
    assert.equal(JSON.stringify(server.attributes).includes('QUERY_OUT_OF_BOUNDS'), false);
  } finally {
    await telemetry.shutdown();
  }
});

test('dispatch spans expose bounded status and error class for TraceQL window metrics', async () => {
  const { telemetry, spanExporter } = runtime();
  try {
    const handler = makeRouterHandler({
      catalog: [{
        shard_id: 'deepseek-0001', provider: 'deepseek', generation_id: 'e'.repeat(64),
        state: 'READY', url: 'https://deepseek.test/search',
      }],
      telemetry,
      fetchImpl: async () => Response.json({ status: 'NO_SIGNAL', generation_id: 'e'.repeat(64), generation_binding: 'router_pinned_v1', hits: [] }),
    });
    const response = await handler.fetch(new Request('https://router.test/search', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'safe-query', provider: 'deepseek' }),
    }));
    assert.equal(response.status, 200);

    const dispatch = spanExporter.getFinishedSpans().find((span) => span.name === 'session_search.shard_dispatch');
    assert.ok(dispatch);
    assert.equal(dispatch.attributes['session_search.status_class'], '2xx');
    assert.equal(dispatch.attributes['session_search.error_class'], 'none');
  } finally {
    await telemetry.shutdown();
  }
});

test('shard extracts router traceparent and continues the same distributed trace', async () => {
  const routerRuntime = runtime();
  const shardSpanExporter = new InMemorySpanExporter();
  const shardMetricExporter = new InMemoryMetricExporter(AggregationTemporality.CUMULATIVE);
  const shardTelemetry = createTelemetryRuntime({
    serviceName: 'session-search-shard',
    serviceVersion: 'test',
    environment: 'candidate',
    traceExporter: shardSpanExporter,
    metricExporter: shardMetricExporter,
    metricExportIntervalMs: 60000,
  });
  try {
    const shardHandler = makeShardHandler(async () => ({
      status: 'FOUND',
      generation_id: 'b'.repeat(64),
      provider: 'deepseek',
      shard_id: 'deepseek-0001',
      hits: [],
    }), { telemetry: shardTelemetry });
    const catalog = [{
      shard_id: 'deepseek-0001', provider: 'deepseek', generation_id: 'b'.repeat(64),
      state: 'READY', url: 'https://deepseek.test/search',
    }];
    const routerHandler = makeRouterHandler({
      catalog,
      telemetry: routerRuntime.telemetry,
      fetchImpl: async (url, init) => shardHandler.fetch(new Request(url, init)),
    });
    const response = await routerHandler.fetch(new Request('https://router.test/search', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'private-query-never-in-telemetry', provider: 'deepseek' }),
    }));
    assert.equal(response.status, 200);

    const routerSpans = routerRuntime.spanExporter.getFinishedSpans();
    const dispatch = routerSpans.find((span) => span.name === 'session_search.shard_dispatch');
    const shardServer = shardSpanExporter.getFinishedSpans().find((span) => span.name === 'session_search.search');
    assert.ok(dispatch);
    assert.ok(shardServer);
    assert.equal(shardServer.spanContext().traceId, dispatch.spanContext().traceId);
    assert.equal(shardServer.parentSpanContext?.spanId, dispatch.spanContext().spanId);
    const payload = JSON.stringify({
      router: routerSpans.map((span) => span.attributes),
      shard: shardSpanExporter.getFinishedSpans().map((span) => span.attributes),
    });
    assert.equal(payload.includes('private-query-never-in-telemetry'), false);
  } finally {
    await Promise.all([routerRuntime.telemetry.shutdown(), shardTelemetry.shutdown()]);
  }
});

test('untrusted inbound tracestate is not exported or propagated to shards', async () => {
  const { telemetry, spanExporter } = runtime();
  try {
    let outboundHeaders;
    const handler = makeRouterHandler({
      catalog: [{
        shard_id: 'deepseek-0001', provider: 'deepseek', generation_id: 'c'.repeat(64),
        state: 'READY', url: 'https://deepseek.test/search',
      }],
      telemetry,
      fetchImpl: async (_url, init) => {
        outboundHeaders = new Headers(init.headers);
        return Response.json({
          generation_binding: 'router_pinned_v1',
          status: 'FOUND', generation_id: 'c'.repeat(64), provider: 'deepseek', shard_id: 'deepseek-0001', hits: [],
        });
      },
    });
    const response = await handler.fetch(new Request('https://router.test/search', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        traceparent: '00-11111111111111111111111111111111-2222222222222222-01',
        tracestate: 'client=private-query-or-token',
      },
      body: JSON.stringify({ query: 'safe-test-query', provider: 'deepseek' }),
    }));
    assert.equal(response.status, 200);
    assert.equal(outboundHeaders?.get('tracestate'), null);
    const server = spanExporter.getFinishedSpans().find((span) => span.name === 'session_search.search');
    assert.ok(server);
    assert.equal(server.parentSpanContext?.traceId, '11111111111111111111111111111111');
    assert.equal(server.parentSpanContext?.spanId, '2222222222222222');
    assert.equal(server.parentSpanContext?.traceState, undefined);
  } finally {
    await telemetry.shutdown();
  }
});

class DelayedSpanExporter {
  constructor(delayMs) {
    this.delayMs = delayMs;
  }

  export(_spans, resultCallback) {
    setTimeout(() => resultCallback({ code: 0 }), this.delayMs);
  }

  async shutdown() {}
}

test('telemetry flush budget cannot consume the shard dispatch deadline', async () => {
  const metricExporter = new InMemoryMetricExporter(AggregationTemporality.CUMULATIVE);
  const telemetry = createTelemetryRuntime({
    serviceName: 'session-search-shard',
    serviceVersion: 'test',
    environment: 'candidate',
    traceExporter: new DelayedSpanExporter(160),
    metricExporter,
    metricExportIntervalMs: 60000,
    flushTimeoutMs: 20,
  });
  try {
    const started = performance.now();
    const response = await telemetry.withRequest(
      new Request('https://shard.test/search'),
      'search',
      async () => Response.json({ status: 'FOUND' }),
    );
    const elapsedMs = performance.now() - started;
    assert.equal(response.status, 200);
    assert.ok(elapsedMs < 100, `telemetry delayed response by ${elapsedMs.toFixed(1)}ms`);
  } finally {
    await telemetry.shutdown();
  }
});

test('MCP feedback span carries a safe join to search traces without leaking identifiers into metrics', async () => {
  const { telemetry, spanExporter, metricExporter } = runtime();
  const baseline = 'a'.repeat(32);
  const resultRef = 'b'.repeat(32);
  const feedbackRef = 'c'.repeat(32);
  try {
    const handler = makeRouterHandler({
      catalog: [], telemetry,
      feedbackWriter: async () => ({ status: 'RECORDED', feedback_ref: feedbackRef }),
      feedbackReader: async () => ({ status: 'FOUND', feedback_ref: feedbackRef,
        feedback: { baseline_request_ref: baseline, result_request_ref: resultRef } }),
    });
    const request = new Request('https://router.test/mcp', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 88, method: 'tools/call', params: {
        name: 'session_feedback', arguments: {
          baseline_request_ref: baseline, result_request_ref: resultRef,
          failure_class: 'low_relevance', operator: { name: 'test_agent', version: 'v1' },
          outcome: 'unknown', notes: 'SENSITIVE_NOTE_NEVER_EXPORT',
        },
      } }),
    });
    const response = await handler.fetch(request);
    assert.equal(response.status, 200);
    assert.equal((await response.json()).result.structuredContent.feedback_ref, feedbackRef);
    const span = spanExporter.getFinishedSpans().find(x => x.name === 'session_search.mcp');
    assert.equal(span.attributes['session_search.feedback_ref'], feedbackRef);
    assert.equal(span.attributes['session_search.feedback_baseline_request_ref'], baseline);
    assert.equal(span.attributes['session_search.feedback_result_request_ref'], resultRef);
    const metrics = JSON.stringify(metricExporter.getMetrics());
    for (const val of [baseline, resultRef, feedbackRef, 'SENSITIVE_NOTE_NEVER_EXPORT']) {
      assert(!metrics.includes(val), 'identifier/notes leaked to metrics');
    }
    const spans = JSON.stringify(spanExporter.getFinishedSpans().map(x => x.attributes));
    assert(!spans.includes('SENSITIVE_NOTE_NEVER_EXPORT'));
  } finally {
    await telemetry.shutdown();
  }
});

function semanticRecorder() {
  const captures = [];
  let flushes = 0;
  return {
    captures,
    flushes: () => flushes,
    events: {
      enabled: true,
      capture(event, properties) { captures.push({ event, properties }); return true; },
      async flush() { flushes += 1; return true; },
      async shutdown() { return true; },
    },
  };
}

test('semantic events use the request lifecycle even when OTLP is disabled', async () => {
  const semantic = semanticRecorder();
  const telemetry = createNoopTelemetry({ semanticEvents: semantic.events });
  const response = await telemetry.withRequest(
    new Request('https://router.test/search'),
    'search',
    async (scope) => {
      scope.emitSemantic('session search completed', {
        route: 'search', request_ref: scope.requestRef, result_status: 'FOUND',
        queried_shards_count: 1, unavailable_shards_count: 0,
      });
      return Response.json({ status: 'FOUND' });
    },
  );
  assert.equal(response.status, 200);
  assert.equal(semantic.captures.length, 1);
  assert.equal(semantic.captures[0].event, 'session search completed');
  assert.match(semantic.captures[0].properties.request_ref, /^[0-9a-f]{32}$/);
  assert.equal(semantic.flushes(), 1);
});

test('router emits search semantic outcome without query text', async () => {
  const semantic = semanticRecorder();
  const telemetry = createNoopTelemetry({ semanticEvents: semantic.events });
  const handler = makeRouterHandler({
    telemetry, resultShape: 'flat_v1',
    catalog: [{
      shard_id: 'deepseek-0001', provider: 'deepseek', generation_id: 'a'.repeat(64),
      state: 'READY', url: 'https://shard.test/search',
    }],
    fetchImpl: async () => Response.json({
      generation_binding: 'router_pinned_v1', status: 'FOUND',
      generation_id: 'a'.repeat(64), provider: 'deepseek', shard_id: 'deepseek-0001', hits: [],
    }),
  });
  const response = await handler.fetch(new Request('https://router.test/search', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'SECRET_QUERY_NEVER_CAPTURE' }),
  }));
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.deepEqual(semantic.captures, [{
    event: 'session search completed',
    properties: {
      route: 'search', request_ref: body.request_ref, result_status: body.status,
      queried_shards_count: 1, unavailable_shards_count: 0,
    },
  }]);
  assert.equal(JSON.stringify(semantic.captures).includes('SECRET_QUERY_NEVER_CAPTURE'), false);
});

test('internal grouped canary remains OTel-only and emits no semantic event', async () => {
  const semantic = semanticRecorder();
  const telemetry = createNoopTelemetry({ semanticEvents: semantic.events });
  const handler = makeRouterHandler({
    telemetry, groupedCanaryEnabled: true, catalog: [],
    fetchImpl: async () => { throw new Error('empty catalog must not dispatch'); },
  });
  const response = await handler.fetch(new Request('https://router.test/internal/grouped-search', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ query: 'canary', search_mode: 'recall', limit: 2, candidate_budget: 6 }),
  }));
  assert.equal(response.status, 200);
  assert.deepEqual(semantic.captures, []);
});

test('router emits bounded coverage classification semantics', async () => {
  const semantic = semanticRecorder();
  const telemetry = createNoopTelemetry({ semanticEvents: semantic.events });
  const handler = makeRouterHandler({
    telemetry,
    catalog: [{
      shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'b'.repeat(64),
      state: 'READY', url: 'https://shard.test/search', time_min: 100, time_max: 200,
    }],
  });
  const response = await handler.fetch(new Request('https://router.test/coverage', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ provider: 'chatgpt', from_time: 120, to_time: 180 }),
  }));
  const body = await response.json();
  assert.equal(response.status, 200);
  assert.deepEqual(semantic.captures, [{
    event: 'session coverage classified',
    properties: {
      route: 'coverage', request_ref: body.request_ref,
      window_fit: body.coverage.window_fit,
      source_currentness: body.coverage.source_currentness,
    },
  }]);
});

test('router emits feedback status but never feedback content or feedback ref', async () => {
  const semantic = semanticRecorder();
  const telemetry = createNoopTelemetry({ semanticEvents: semantic.events });
  const feedbackRef = 'e'.repeat(32);
  const handler = makeRouterHandler({
    catalog: [], telemetry,
    feedbackWriter: async () => ({ status: 'RECORDED', feedback_ref: feedbackRef }),
  });
  const response = await handler.fetch(new Request('https://router.test/mcp', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 99, method: 'tools/call', params: {
      name: 'session_feedback', arguments: {
        baseline_request_ref: 'a'.repeat(32), result_request_ref: 'b'.repeat(32),
        failure_class: 'low_relevance', operator: { name: 'test_agent', version: 'v1' },
        outcome: 'unknown', notes: 'SENSITIVE_FEEDBACK_NEVER_CAPTURE',
      },
    } }),
  }));
  assert.equal(response.status, 200);
  assert.equal(semantic.captures.length, 1);
  assert.deepEqual(semantic.captures[0], {
    event: 'session feedback submitted',
    properties: { route: 'mcp', request_ref: semantic.captures[0].properties.request_ref, feedback_status: 'RECORDED' },
  });
  assert.match(semantic.captures[0].properties.request_ref, /^[0-9a-f]{32}$/);
  const encoded = JSON.stringify(semantic.captures);
  assert.equal(encoded.includes(feedbackRef), false);
  assert.equal(encoded.includes('SENSITIVE_FEEDBACK_NEVER_CAPTURE'), false);
});

test('semantic adapter exceptions cannot change the request outcome', async () => {
  const telemetry = createNoopTelemetry({
    semanticEvents: {
      capture() { throw new Error('adapter-capture-broke'); },
      async flush() { throw new Error('adapter-flush-broke'); },
      async shutdown() { throw new Error('adapter-shutdown-broke'); },
    },
  });
  const response = await telemetry.withRequest(
    new Request('https://router.test/search'),
    'search',
    async (scope) => {
      scope.emitSemantic('session search completed', {
        route: 'search', request_ref: scope.requestRef, result_status: 'FOUND',
        queried_shards_count: 1, unavailable_shards_count: 0,
      });
      return Response.json({ status: 'FOUND' });
    },
  );
  assert.equal(response.status, 200);
  assert.equal((await response.json()).status, 'FOUND');
  await assert.doesNotReject(() => telemetry.shutdown());
});

test('MCP session_search emits the same bounded semantic search outcome', async () => {
  const semantic = semanticRecorder();
  const telemetry = createNoopTelemetry({ semanticEvents: semantic.events });
  const handler = makeRouterHandler({
    telemetry, resultShape: 'flat_v1',
    catalog: [{
      shard_id: 'deepseek-0001', provider: 'deepseek', generation_id: 'c'.repeat(64),
      state: 'READY', url: 'https://shard.test/search',
    }],
    fetchImpl: async () => Response.json({
      generation_binding: 'router_pinned_v1', status: 'FOUND',
      generation_id: 'c'.repeat(64), provider: 'deepseek', shard_id: 'deepseek-0001', hits: [],
    }),
  });
  const response = await handler.fetch(new Request('https://router.test/mcp', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 101, method: 'tools/call', params: {
      name: 'session_search', arguments: { query: 'MCP_SECRET_QUERY_NEVER_CAPTURE' },
    } }),
  }));
  const body = await response.json();
  assert.equal(response.status, 200);
  const structured = body.result.structuredContent;
  assert.equal(body.result.isError, false);
  assert.deepEqual(semantic.captures, [{
    event: 'session search completed',
    properties: {
      route: 'mcp', request_ref: structured.request_ref, result_status: structured.status,
      queried_shards_count: 1, unavailable_shards_count: 0,
    },
  }]);
  assert.equal(JSON.stringify(semantic.captures).includes('MCP_SECRET_QUERY_NEVER_CAPTURE'), false);
});

test('MCP session_coverage emits bounded semantic coverage classification', async () => {
  const semantic = semanticRecorder();
  const telemetry = createNoopTelemetry({ semanticEvents: semantic.events });
  const handler = makeRouterHandler({
    telemetry,
    catalog: [{
      shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'd'.repeat(64),
      state: 'READY', url: 'https://shard.test/search', time_min: 100, time_max: 200,
    }],
  });
  const response = await handler.fetch(new Request('https://router.test/mcp', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 102, method: 'tools/call', params: {
      name: 'session_coverage', arguments: { provider: 'chatgpt', from_time: 120, to_time: 180 },
    } }),
  }));
  const body = await response.json();
  assert.equal(response.status, 200);
  const structured = body.result.structuredContent;
  assert.equal(body.result.isError, false);
  assert.deepEqual(semantic.captures, [{
    event: 'session coverage classified',
    properties: {
      route: 'mcp', request_ref: structured.request_ref,
      window_fit: structured.coverage.window_fit,
      source_currentness: structured.coverage.source_currentness,
    },
  }]);
});
