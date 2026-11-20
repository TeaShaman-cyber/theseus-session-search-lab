import test from 'node:test';
import assert from 'node:assert/strict';

import { createPostHogEventsFromEnv, createSemanticEventsRuntime } from '../posthog-events.mjs';

const REF = 'a'.repeat(32);

function fakeClient({ flush = async () => {}, capture = null } = {}) {
  const calls = [];
  const handlers = new Map();
  return {
    calls,
    handlers,
    capture: capture ?? ((value) => calls.push(value)),
    flush,
    on(name, fn) { handlers.set(name, fn); },
  };
}

test('missing PostHog token yields a noop runtime', async () => {
  let factoryCalls = 0;
  const events = createPostHogEventsFromEnv({
    env: {},
    clientFactory: () => { factoryCalls += 1; return fakeClient(); },
  });
  assert.equal(events.enabled, false);
  assert.equal(await events.capture('session search completed', { requestRef: REF, properties: {} }), false);
  assert.equal(factoryCalls, 0);
});

test('search event is personless and contains only bounded service semantics', async () => {
  const client = fakeClient();
  const events = createSemanticEventsRuntime({
    client, environment: 'production', serviceVersion: 'abc123', flushTimeoutMs: 50,
  });
  const accepted = await events.capture('session search completed', {
    requestRef: REF,
    properties: {
      route: 'search', result_status: 'FOUND', queried_shards_count: 2, unavailable_shards_count: 0,
    },
  });
  assert.equal(accepted, true);
  assert.equal(client.calls.length, 1);
  assert.deepEqual(client.calls[0], {
    distinctId: REF,
    event: 'session search completed',
    properties: {
      route: 'search', result_status: 'FOUND', queried_shards_count: 2, unavailable_shards_count: 0,
      environment: 'production', service_version: 'abc123', request_ref: REF,
    },
    options: { process_person_profile: false },
  });
});

test('PARTIAL uses the dedicated partial event', async () => {
  const client = fakeClient();
  const events = createSemanticEventsRuntime({ client, environment: 'candidate', serviceVersion: 'test' });
  await events.capture('session search partial', {
    requestRef: REF,
    properties: { route: 'mcp', result_status: 'PARTIAL', queried_shards_count: 1, unavailable_shards_count: 1 },
  });
  assert.equal(client.calls[0].event, 'session search partial');
});

test('feedback event accepts only storage status and route', async () => {
  const client = fakeClient();
  const events = createSemanticEventsRuntime({ client, environment: 'candidate', serviceVersion: 'test' });
  assert.equal(await events.capture('session feedback submitted', {
    requestRef: REF, properties: { route: 'mcp', result_status: 'RECORDED' },
  }), true);
  assert.equal(client.calls.length, 1);
});

test('unknown events, invalid request refs, and unsafe properties are dropped', async () => {
  const client = fakeClient();
  const events = createSemanticEventsRuntime({ client, environment: 'candidate', serviceVersion: 'test' });
  assert.equal(await events.capture('query text exported', { requestRef: REF, properties: {} }), false);
  assert.equal(await events.capture('session search completed', { requestRef: 'user@example.com', properties: { route: 'search', result_status: 'FOUND', queried_shards_count: 1, unavailable_shards_count: 0 } }), false);
  assert.equal(await events.capture('session search completed', { requestRef: REF, properties: { route: 'search', result_status: 'FOUND', query: 'private text' } }), false);
  assert.equal(client.calls.length, 0);
});

test('capture and flush failures never escape the semantic lane', async () => {
  const logs = [];
  const badCapture = fakeClient({ capture() { throw new Error('secret capture failure'); } });
  const captureEvents = createSemanticEventsRuntime({ client: badCapture, logger: value => logs.push(String(value)) });
  assert.equal(await captureEvents.capture('session search completed', { requestRef: REF, properties: { route: 'search', result_status: 'FOUND', queried_shards_count: 1, unavailable_shards_count: 0 } }), false);

  const badFlush = fakeClient({ flush: async () => { throw new Error('secret flush failure'); } });
  const flushEvents = createSemanticEventsRuntime({ client: badFlush, logger: value => logs.push(String(value)) });
  assert.equal(await flushEvents.capture('session search completed', { requestRef: REF, properties: { route: 'search', result_status: 'FOUND', queried_shards_count: 1, unavailable_shards_count: 0 } }), true);
  assert.equal(logs.some(line => line.includes('secret')), false);
});

test('flush wait is bounded when the SDK never resolves', async () => {
  const client = fakeClient({ flush: async () => new Promise(() => {}) });
  const events = createSemanticEventsRuntime({ client, flushTimeoutMs: 10 });
  const started = Date.now();
  assert.equal(await events.capture('session search completed', { requestRef: REF, properties: { route: 'search', result_status: 'FOUND', queried_shards_count: 1, unavailable_shards_count: 0 } }), true);
  assert.ok(Date.now() - started < 200);
});

test('coverage classification accepts only bounded window-fit semantics', async () => {
  const client = fakeClient();
  const events = createSemanticEventsRuntime({ client, environment: 'candidate', serviceVersion: 'test' });
  assert.equal(await events.capture('session coverage classified', {
    requestRef: REF, properties: { route: 'coverage', result_status: 'OBSERVED_POSSIBLE' },
  }), true);
  assert.equal(client.calls[0].event, 'session coverage classified');
  assert.equal(client.calls[0].properties.result_status, 'OBSERVED_POSSIBLE');
  assert.equal(await events.capture('session coverage classified', {
    requestRef: REF, properties: { route: 'coverage', result_status: 'VERIFIED_TO_PROVIDER_REPORTED_WATERMARK' },
  }), false);
});


test('configured env constructs one client with the bounded service context', async () => {
  const client = fakeClient();
  let seen;
  const events = createPostHogEventsFromEnv({
    env: {
      POSTHOG_PROJECT_TOKEN: 'phc_test',
      POSTHOG_HOST: 'https://eu.i.posthog.com',
      SESSION_SEARCH_ENVIRONMENT: 'production',
      SESSION_SEARCH_SERVICE_VERSION: 'deadbeef',
    },
    clientFactory: (token, options) => { seen = { token, options }; return client; },
  });
  assert.equal(events.enabled, true);
  assert.deepEqual(seen, { token: 'phc_test', options: { host: 'https://eu.i.posthog.com' } });
  await events.capture('session feedback submitted', {
    requestRef: REF, properties: { route: 'mcp', result_status: 'UNCHANGED' },
  });
  assert.equal(client.calls[0].properties.environment, 'production');
  assert.equal(client.calls[0].properties.service_version, 'deadbeef');
});
