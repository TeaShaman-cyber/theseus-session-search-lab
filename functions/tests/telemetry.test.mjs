import test from 'node:test';
import assert from 'node:assert/strict';

import { InMemoryMetricExporter, AggregationTemporality } from '@opentelemetry/sdk-metrics';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace';
import { createNoopTelemetry, createTelemetryRuntime } from '../telemetry.mjs';
import { makeRouterHandler } from '../router/handler.mjs';
import { makeShardHandler } from '../shard/handler.mjs';

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
  const response = await telemetry.withRequest(
    new Request('https://router.test/search?secret=must-not-matter'),
    'search',
    async () => Response.json({ status: 'FOUND' }),
  );
  assert.equal(response.status, 200);
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
          status: 'FOUND', generation_id: 'a'.repeat(64), provider: 'deepseek', shard_id: 'deepseek-0001', hits: [],
        });
      },
    });
    const response = await handler.fetch(new Request('https://router.test/search?q=super-secret-query', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ query: 'super-secret-query' }),
    }));
    assert.equal(response.status, 200);
    assert.match(traceparent ?? '', /^00-[0-9a-f]{32}-[0-9a-f]{16}-0[01]$/);

    const spans = spanExporter.getFinishedSpans();
    assert.equal(spans.length, 2);
    const server = spans.find((span) => span.name === 'session_search.search');
    const dispatch = spans.find((span) => span.name === 'session_search.shard_dispatch');
    assert.ok(server);
    assert.ok(dispatch);
    assert.equal(server.spanContext().traceId, dispatch.spanContext().traceId);
    assert.equal(dispatch.parentSpanContext?.spanId, server.spanContext().spanId);

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
    assert.match(metricPayload, /deepseek-0001/);

    const counts = flushes();
    assert.ok(counts.spanFlushes >= 1);
    assert.ok(counts.metricFlushes >= 1);
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
