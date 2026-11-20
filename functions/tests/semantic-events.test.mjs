import test from 'node:test';
import assert from 'node:assert/strict';
import { createNoopSemanticEvents, createPostHogSemanticEvents, createSemanticEventsFromEnv } from '../semantic-events.mjs';

function fakeClient({ flushImpl = async () => {} } = {}) {
  const captures = [];
  const handlers = new Map();
  return {
    captures, handlers,
    capture(value) { captures.push(value); },
    flush: flushImpl,
    async shutdown() {},
    on(name, fn) { handlers.set(name, fn); },
  };
}

test('missing token is a true noop', async () => {
  let constructed = false;
  const events = createSemanticEventsFromEnv({ env: {}, clientFactory() { constructed = true; } });
  assert.equal(events.enabled, false);
  assert.equal(events.capture('session search completed', {}), false);
  assert.equal(await events.flush(), true);
  assert.equal(constructed, false);
});

test('configured factory disables unrelated SDK behavior and retries', () => {
  let seen;
  const client = fakeClient();
  const events = createSemanticEventsFromEnv({
    env: { POSTHOG_PROJECT_TOKEN: 'phc_test', POSTHOG_HOST: 'https://us.i.posthog.com/', SESSION_SEARCH_SERVICE_VERSION: 'v-test', SESSION_SEARCH_ENVIRONMENT: 'candidate' },
    flushTimeoutMs: 75,
    clientFactory(token, options) { seen = { token, options }; return client; },
  });
  assert.equal(events.enabled, true);
  assert.equal(seen.token, 'phc_test');
  assert.equal(seen.options.host, 'https://us.i.posthog.com');
  assert.equal(seen.options.flushInterval, 0);
  assert.equal(seen.options.captureMaxRetries, 0);
  assert.equal(seen.options.fetchRetryCount, 0);
  assert.equal(seen.options.requestTimeout, 75);
  assert.equal(seen.options.preloadFeatureFlags, false);
  assert.equal(seen.options.featureFlagsPollingInterval, null);
  assert.equal(seen.options.enableExceptionAutocapture, false);
  assert.equal(seen.options.captureTracingHeaders, false);
  assert.equal(seen.options.disableGeoip, true);
  assert.equal(client.handlers.has('error'), true);
});

test('capture is personless and sends only strict allowlisted properties', () => {
  const client = fakeClient();
  const events = createPostHogSemanticEvents({ client, serviceVersion: 'v-test', environment: 'candidate' });
  assert.equal(events.capture('session search completed', {
    route: 'search', request_ref: 'a'.repeat(32), result_status: 'FOUND', queried_shards_count: 2, unavailable_shards_count: 0,
  }), true);
  assert.deepEqual(client.captures, [{
    event: 'session search completed',
    properties: { environment: 'candidate', service_version: 'v-test', route: 'search', request_ref: 'a'.repeat(32), result_status: 'FOUND', queried_shards_count: 2, unavailable_shards_count: 0 },
    options: { process_person_profile: false },
  }]);
  assert.equal('distinctId' in client.captures[0], false);
});

test('unknown, unsafe, or invalid event properties reject the whole event', () => {
  const logs = [];
  const client = fakeClient();
  const events = createPostHogSemanticEvents({ client, log: (v) => logs.push(v) });
  assert.equal(events.capture('unknown event', {}), false);
  assert.equal(events.capture('session search completed', { route: 'search', request_ref: 'b'.repeat(32), result_status: 'FOUND', queried_shards_count: 1, unavailable_shards_count: 0, query: 'secret' }), false);
  assert.equal(events.capture('session search completed', { route: 'search', request_ref: 'user@example.com', result_status: 'FOUND', queried_shards_count: 1, unavailable_shards_count: 0 }), false);
  assert.equal(client.captures.length, 0);
  assert.ok(logs.every((v) => v.event === 'session_search_posthog_event_rejected'));
});

test('coverage and feedback schemas stay bounded', () => {
  const client = fakeClient();
  const events = createPostHogSemanticEvents({ client, serviceVersion: 'v', environment: 'e' });
  assert.equal(events.capture('session coverage classified', { route: 'coverage', request_ref: 'c'.repeat(32), window_fit: 'OBSERVED_POSSIBLE', source_currentness: 'UNKNOWN_WITHOUT_SOURCE_WATERMARK' }), true);
  assert.equal(events.capture('session feedback submitted', { route: 'mcp', request_ref: 'd'.repeat(32), feedback_status: 'RECORDED' }), true);
  assert.equal(client.captures.length, 2);
});

test('capture and flush failures are contained and flush is budget bounded', async () => {
  const logs = [];
  const client = fakeClient({ flushImpl: () => new Promise(() => {}) });
  client.capture = () => { throw new Error('capture secret'); };
  const events = createPostHogSemanticEvents({ client, flushTimeoutMs: 10, log: (v) => logs.push(v) });
  assert.equal(events.capture('session search completed', { route: 'search', request_ref: 'e'.repeat(32), result_status: 'FOUND', queried_shards_count: 1, unavailable_shards_count: 0 }), false);
  const started = Date.now();
  assert.equal(await events.flush(), false);
  assert.ok(Date.now() - started < 200);
  assert.ok(logs.some((v) => v.event === 'session_search_posthog_capture_failed'));
  assert.ok(logs.some((v) => v.event === 'session_search_posthog_flush_timeout'));
});

test('noop runtime is idempotent', async () => {
  const events = createNoopSemanticEvents();
  assert.equal(events.enabled, false);
  assert.equal(events.capture('session search completed', {}), false);
  assert.equal(await events.flush(), true);
  assert.equal(await events.shutdown(), true);
});
