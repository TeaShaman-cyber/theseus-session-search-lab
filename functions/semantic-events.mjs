import { PostHog } from 'posthog-node/edge';

const DEFAULT_HOST = 'https://us.i.posthog.com';
const DEFAULT_FLUSH_TIMEOUT_MS = 100;
const SAFE_LABEL = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const REQUEST_REF = /^[0-9a-f]{32}$/;

const EVENT_SCHEMAS = {
  'session search completed': {
    required: ['route', 'request_ref', 'result_status', 'queried_shards_count', 'unavailable_shards_count'],
    validators: {
      route: (v) => new Set(['search', 'mcp']).has(v) ? v : undefined,
      request_ref: safeRequestRef,
      result_status: (v) => new Set(['FOUND', 'NO_SIGNAL']).has(v) ? v : undefined,
      queried_shards_count: safeCount,
      unavailable_shards_count: safeCount,
    },
  },
  'session search partial': {
    required: ['route', 'request_ref', 'result_status', 'queried_shards_count', 'unavailable_shards_count'],
    validators: {
      route: (v) => new Set(['search', 'mcp']).has(v) ? v : undefined,
      request_ref: safeRequestRef,
      result_status: (v) => v === 'PARTIAL' ? v : undefined,
      queried_shards_count: safeCount,
      unavailable_shards_count: safeCount,
    },
  },
  'session coverage classified': {
    required: ['route', 'request_ref', 'window_fit', 'source_currentness'],
    validators: {
      route: (v) => new Set(['coverage', 'mcp']).has(v) ? v : undefined,
      request_ref: safeRequestRef,
      window_fit: (v) => new Set(['OBSERVED_POSSIBLE', 'OUTSIDE_OBSERVED_INDEX_RANGE', 'UNKNOWN_COVERAGE']).has(v) ? v : undefined,
      source_currentness: (v) => new Set([
        'VERIFIED_TO_PROVIDER_REPORTED_WATERMARK',
        'STALE_AFTER_PROVIDER_REPORTED_WATERMARK',
        'UNKNOWN_WITHOUT_SOURCE_WATERMARK',
      ]).has(v) ? v : undefined,
    },
  },
  'session feedback submitted': {
    required: ['route', 'request_ref', 'feedback_status'],
    validators: {
      route: (v) => v === 'mcp' ? v : undefined,
      request_ref: safeRequestRef,
      feedback_status: (v) => new Set(['RECORDED', 'UNCHANGED']).has(v) ? v : undefined,
    },
  },
};

function safeRequestRef(value) {
  return typeof value === 'string' && REQUEST_REF.test(value) ? value : undefined;
}

function safeCount(value) {
  return Number.isInteger(value) && value >= 0 && value <= 10000 ? value : undefined;
}

function safeCommon(value, fallback) {
  return typeof value === 'string' && SAFE_LABEL.test(value) ? value : fallback;
}

function sanitizeEvent(event, properties, { serviceVersion, environment }) {
  const schema = EVENT_SCHEMAS[event];
  if (!schema || properties == null || typeof properties !== 'object' || Array.isArray(properties)) return null;
  const keys = Object.keys(properties);
  if (keys.some((key) => !(key in schema.validators))) return null;
  if (schema.required.some((key) => !keys.includes(key))) return null;
  const safe = {
    environment: safeCommon(environment, 'candidate'),
    service_version: safeCommon(serviceVersion, 'unknown'),
  };
  for (const [key, value] of Object.entries(properties)) {
    const validated = schema.validators[key](value);
    if (validated === undefined) return null;
    safe[key] = validated;
  }
  return safe;
}

function defaultLog(value) {
  console.error(JSON.stringify(value));
}

export function createNoopSemanticEvents() {
  return {
    enabled: false,
    capture() { return false; },
    async flush() { return true; },
    async shutdown() { return true; },
  };
}

export function createPostHogSemanticEvents({
  client,
  serviceVersion = 'unknown',
  environment = 'candidate',
  flushTimeoutMs = DEFAULT_FLUSH_TIMEOUT_MS,
  log = defaultLog,
}) {
  if (!client || typeof client.capture !== 'function' || typeof client.flush !== 'function') {
    throw new Error('POSTHOG_CLIENT_INVALID');
  }
  if (!Number.isFinite(flushTimeoutMs) || flushTimeoutMs <= 0) {
    throw new Error('POSTHOG_FLUSH_TIMEOUT_INVALID');
  }

  function capture(event, properties = {}) {
    const safeProperties = sanitizeEvent(event, properties, { serviceVersion, environment });
    if (safeProperties === null) {
      log({ event: 'session_search_posthog_event_rejected' });
      return false;
    }
    try {
      client.capture({
        event,
        properties: safeProperties,
        options: { process_person_profile: false },
      });
      return true;
    } catch {
      log({ event: 'session_search_posthog_capture_failed' });
      return false;
    }
  }

  async function flush() {
    let timeoutId;
    try {
      const outcome = await Promise.race([
        Promise.resolve().then(() => client.flush()).then(() => 'ok', () => 'failed'),
        new Promise((resolve) => { timeoutId = setTimeout(() => resolve('timeout'), flushTimeoutMs); }),
      ]);
      if (outcome === 'ok') return true;
      log({ event: outcome === 'timeout' ? 'session_search_posthog_flush_timeout' : 'session_search_posthog_flush_failed' });
      return false;
    } catch {
      log({ event: 'session_search_posthog_flush_failed' });
      return false;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  async function shutdown() {
    try {
      await client.shutdown?.(flushTimeoutMs);
      return true;
    } catch {
      log({ event: 'session_search_posthog_shutdown_failed' });
      return false;
    }
  }

  return { enabled: true, capture, flush, shutdown };
}

export function createSemanticEventsFromEnv({
  env = process.env,
  flushTimeoutMs = DEFAULT_FLUSH_TIMEOUT_MS,
  clientFactory = (token, options) => new PostHog(token, options),
  log = defaultLog,
} = {}) {
  const token = String(env.POSTHOG_PROJECT_TOKEN ?? '').trim();
  if (!token) return createNoopSemanticEvents();
  const host = String(env.POSTHOG_HOST ?? DEFAULT_HOST).trim().replace(/\/+$/, '') || DEFAULT_HOST;
  const client = clientFactory(token, {
    host,
    flushInterval: 0,
    maxQueueSize: 100,
    captureMaxRetries: 0,
    fetchRetryCount: 0,
    featureFlagsRequestMaxRetries: 0,
    requestTimeout: flushTimeoutMs,
    featureFlagsRequestTimeoutMs: flushTimeoutMs,
    remoteConfigRequestTimeoutMs: flushTimeoutMs,
    preloadFeatureFlags: false,
    featureFlagsPollingInterval: null,
    disableSurveys: true,
    enableExceptionAutocapture: false,
    captureTracingHeaders: false,
    disableGeoip: true,
  });
  client.on?.('error', () => log({ event: 'session_search_posthog_client_error' }));
  return createPostHogSemanticEvents({
    client,
    serviceVersion: env.SESSION_SEARCH_SERVICE_VERSION ?? 'unknown',
    environment: env.SESSION_SEARCH_ENVIRONMENT ?? 'candidate',
    flushTimeoutMs,
    log,
  });
}
