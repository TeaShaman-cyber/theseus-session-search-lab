import { PostHog } from 'posthog-node';

const DEFAULT_HOST = 'https://us.i.posthog.com';
const DEFAULT_FLUSH_TIMEOUT_MS = 100;
const REQUEST_REF = /^[0-9a-f]{32}$/;
const SAFE_TEXT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

const EVENT_RULES = new Map([
  ['session search completed', {
    keys: new Set(['route', 'result_status', 'queried_shards_count', 'unavailable_shards_count']),
    routes: new Set(['search', 'mcp']),
    statuses: new Set(['FOUND', 'NO_SIGNAL']),
  }],
  ['session search partial', {
    keys: new Set(['route', 'result_status', 'queried_shards_count', 'unavailable_shards_count']),
    routes: new Set(['search', 'mcp']),
    statuses: new Set(['PARTIAL']),
  }],
  ['session feedback submitted', {
    keys: new Set(['route', 'result_status']),
    routes: new Set(['mcp']),
    statuses: new Set(['RECORDED', 'UNCHANGED']),
  }],
  ['session coverage classified', {
    keys: new Set(['route', 'result_status']),
    routes: new Set(['coverage', 'mcp']),
    statuses: new Set(['OBSERVED_POSSIBLE', 'OUTSIDE_OBSERVED_INDEX_RANGE', 'UNKNOWN_COVERAGE']),
  }],
]);

function safeText(value, fallback = 'unknown') {
  return typeof value === 'string' && SAFE_TEXT.test(value) ? value : fallback;
}

function safeCount(value) {
  return Number.isInteger(value) && value >= 0 && value <= 100 ? value : null;
}

function sanitize(event, requestRef, properties, environment, serviceVersion) {
  const rule = EVENT_RULES.get(event);
  if (!rule || !REQUEST_REF.test(String(requestRef ?? '')) || properties == null || typeof properties !== 'object' || Array.isArray(properties)) return null;
  if (Object.keys(properties).some((key) => !rule.keys.has(key))) return null;
  if (!rule.routes.has(properties.route) || !rule.statuses.has(properties.result_status)) return null;

  const clean = { route: properties.route, result_status: properties.result_status };
  if (rule.keys.has('queried_shards_count')) {
    const queried = safeCount(properties.queried_shards_count);
    const unavailable = safeCount(properties.unavailable_shards_count);
    if (queried === null || unavailable === null) return null;
    clean.queried_shards_count = queried;
    clean.unavailable_shards_count = unavailable;
  }
  clean.environment = safeText(environment);
  clean.service_version = safeText(serviceVersion);
  clean.request_ref = requestRef;
  return clean;
}

function diagnostic(logger, event) {
  try { logger(JSON.stringify({ event })); } catch {}
}

export function createNoopPostHogEvents() {
  return { enabled: false, async capture() { return false; }, async shutdown() {} };
}

export function createSemanticEventsRuntime({
  client, environment = 'candidate', serviceVersion = 'unknown',
  flushTimeoutMs = DEFAULT_FLUSH_TIMEOUT_MS, logger = console.error,
} = {}) {
  if (!client || typeof client.capture !== 'function' || typeof client.flush !== 'function') throw new Error('POSTHOG_CLIENT_INVALID');
  if (!Number.isFinite(flushTimeoutMs) || flushTimeoutMs <= 0) throw new Error('POSTHOG_FLUSH_TIMEOUT_INVALID');

  if (typeof client.on === 'function') client.on('error', () => diagnostic(logger, 'session_search_posthog_delivery_failed'));

  async function flushBounded() {
    let timer;
    try {
      const outcome = await Promise.race([
        Promise.resolve().then(() => client.flush()).then(() => 'flushed', () => 'failed'),
        new Promise((resolve) => { timer = setTimeout(() => resolve('timeout'), flushTimeoutMs); }),
      ]);
      if (outcome === 'failed') diagnostic(logger, 'session_search_posthog_flush_failed');
      else if (outcome === 'timeout') diagnostic(logger, 'session_search_posthog_flush_timeout');
    } finally {
      clearTimeout(timer);
    }
  }

  return {
    enabled: true,
    async capture(event, { requestRef, properties = {} } = {}) {
      const clean = sanitize(event, requestRef, properties, environment, serviceVersion);
      if (!clean) {
        diagnostic(logger, 'session_search_posthog_event_dropped');
        return false;
      }
      try {
        client.capture({
          distinctId: requestRef, event, properties: clean,
          options: { process_person_profile: false },
        });
      } catch {
        diagnostic(logger, 'session_search_posthog_capture_failed');
        return false;
      }
      await flushBounded();
      return true;
    },
    async shutdown() {
      if (typeof client.shutdown !== 'function') return;
      try { await client.shutdown(flushTimeoutMs); } catch { diagnostic(logger, 'session_search_posthog_shutdown_failed'); }
    },
  };
}

export function createPostHogEventsFromEnv({
  env = process.env,
  clientFactory = (token, options) => new PostHog(token, options),
  flushTimeoutMs = DEFAULT_FLUSH_TIMEOUT_MS,
  logger = console.error,
} = {}) {
  const token = env.POSTHOG_PROJECT_TOKEN;
  if (typeof token !== 'string' || token.length === 0) return createNoopPostHogEvents();
  const host = env.POSTHOG_HOST || DEFAULT_HOST;
  let client;
  try { client = clientFactory(token, { host }); } catch {
    diagnostic(logger, 'session_search_posthog_init_failed');
    return createNoopPostHogEvents();
  }
  return createSemanticEventsRuntime({
    client,
    environment: env.SESSION_SEARCH_ENVIRONMENT ?? 'candidate',
    serviceVersion: env.SESSION_SEARCH_SERVICE_VERSION ?? 'unknown',
    flushTimeoutMs, logger,
  });
}
