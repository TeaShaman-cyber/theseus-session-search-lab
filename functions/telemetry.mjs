import {
  ROOT_CONTEXT,
  SpanKind,
  SpanStatusCode,
  trace,
} from '@opentelemetry/api';
import { W3CTraceContextPropagator } from '@opentelemetry/core';
import { OTLPMetricExporter } from '@opentelemetry/exporter-metrics-otlp-http';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { MeterProvider, PeriodicExportingMetricReader } from '@opentelemetry/sdk-metrics';
import { SimpleSpanProcessor, TracerProvider } from '@opentelemetry/sdk-trace';

const propagator = new W3CTraceContextPropagator();
const ERROR_CLASSES = new Set(['none', 'validation', 'not_found', 'upstream', 'timeout', 'internal', 'not_ready']);
const ROUTES = new Set(['health', 'search', 'mcp', 'not_found']);
const RESULT_STATUSES = new Set(['OK', 'FOUND', 'NO_SIGNAL', 'PARTIAL', 'READY', 'BUILDING', 'ACTIVATED', 'ERROR', 'UNKNOWN']);
const SAFE_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const GENERATION_ID = /^[0-9a-f]{64}$/;
const DEFAULT_FLUSH_TIMEOUT_MS = 250;

const headerGetter = {
  keys(carrier) {
    return [...carrier.keys()];
  },
  get(carrier, key) {
    return carrier.get(key) ?? undefined;
  },
};

const headerSetter = {
  set(carrier, key, value) {
    carrier.set(key, String(value));
  },
};

function extractSanitizedParentContext(request) {
  const extracted = propagator.extract(ROOT_CONTEXT, request.headers, headerGetter);
  const spanContext = trace.getSpanContext(extracted);
  if (!spanContext) return ROOT_CONTEXT;
  return trace.setSpanContext(ROOT_CONTEXT, {
    traceId: spanContext.traceId,
    spanId: spanContext.spanId,
    traceFlags: spanContext.traceFlags,
    isRemote: spanContext.isRemote,
  });
}

function statusClass(status) {
  if (!Number.isInteger(status)) return 'unknown';
  if (status >= 200 && status < 300) return '2xx';
  if (status >= 400 && status < 500) return '4xx';
  if (status >= 500 && status < 600) return '5xx';
  return 'other';
}

function safeRoute(value) {
  return ROUTES.has(value) ? value : 'not_found';
}

function safeErrorClass(value) {
  return ERROR_CLASSES.has(value) ? value : 'internal';
}

function safeResultStatus(value) {
  return RESULT_STATUSES.has(value) ? value : 'UNKNOWN';
}

function safeId(value) {
  return typeof value === 'string' && SAFE_ID.test(value) ? value : undefined;
}

function safeGeneration(value) {
  return typeof value === 'string' && GENERATION_ID.test(value) ? value : undefined;
}

function parseHeaderEnv(raw) {
  if (!raw) return undefined;
  const headers = {};
  for (const item of String(raw).split(',')) {
    const index = item.indexOf('=');
    if (index <= 0) continue;
    const key = decodeURIComponent(item.slice(0, index).trim());
    const value = decodeURIComponent(item.slice(index + 1).trim());
    if (key) headers[key] = value;
  }
  return Object.keys(headers).length ? headers : undefined;
}

function metricAttributes({ route, status, errorClass = 'none', resultStatus, provider, shardId }) {
  const attributes = {
    route: safeRoute(route),
    status_class: statusClass(status),
    error_class: safeErrorClass(errorClass),
  };
  if (resultStatus) attributes.result_status = safeResultStatus(resultStatus);
  const safeProvider = safeId(provider);
  const safeShard = safeId(shardId);
  if (safeProvider) attributes.provider = safeProvider;
  if (safeShard) attributes.shard_id = safeShard;
  return attributes;
}

function spanOutcomeAttributes({ status, errorClass = 'none' }) {
  return {
    'session_search.status_class': statusClass(status),
    'session_search.error_class': safeErrorClass(errorClass),
  };
}

function spanResultAttributes({ resultStatus, provider, shardId, generationId, queriedShards, unavailableShards }) {
  const attributes = {};
  if (resultStatus) attributes['session_search.result_status'] = safeResultStatus(resultStatus);
  const safeProvider = safeId(provider);
  const safeShard = safeId(shardId);
  const safeGenerationId = safeGeneration(generationId);
  if (safeProvider) attributes['session_search.provider'] = safeProvider;
  if (safeShard) attributes['session_search.shard_id'] = safeShard;
  if (safeGenerationId) attributes['session_search.generation_id'] = safeGenerationId;
  if (Number.isInteger(queriedShards) && queriedShards >= 0) {
    attributes['session_search.queried_shards_count'] = queriedShards;
  }
  if (Number.isInteger(unavailableShards) && unavailableShards >= 0) {
    attributes['session_search.unavailable_shards_count'] = unavailableShards;
  }
  return attributes;
}

export function createNoopTelemetry() {
  return {
    enabled: false,
    async withRequest(_request, _route, fn) {
      return fn({ context: ROOT_CONTEXT, setResult() {}, setErrorClass() {} });
    },
    async withDispatch(_parentContext, _shard, baseHeaders, fn) {
      return fn(new Headers(baseHeaders));
    },
    async forceFlush() {},
    async shutdown() {},
  };
}

export function createTelemetryRuntime({
  serviceName,
  serviceVersion = 'unknown',
  environment = 'candidate',
  traceExporter,
  metricExporter,
  metricExportIntervalMs = 60000,
  flushTimeoutMs = DEFAULT_FLUSH_TIMEOUT_MS,
}) {
  if (!serviceName || !traceExporter || !metricExporter) {
    throw new Error('TELEMETRY_RUNTIME_CONFIG_INVALID');
  }
  if (!Number.isFinite(flushTimeoutMs) || flushTimeoutMs <= 0) {
    throw new Error('TELEMETRY_FLUSH_TIMEOUT_INVALID');
  }

  const resource = resourceFromAttributes({
    'service.name': serviceName,
    'service.version': serviceVersion,
    'deployment.environment.name': environment,
  });

  const metricReader = new PeriodicExportingMetricReader({
    exporter: metricExporter,
    exportIntervalMillis: metricExportIntervalMs,
    exportTimeoutMillis: flushTimeoutMs,
  });
  const meterProvider = new MeterProvider({ resource, readers: [metricReader] });
  const tracerProvider = new TracerProvider({
    resource,
    spanProcessors: [new SimpleSpanProcessor({ exporter: traceExporter })],
    meterProvider,
  });
  const tracer = tracerProvider.getTracer('theseus-session-search', serviceVersion);
  const meter = meterProvider.getMeter('theseus-session-search', serviceVersion);
  const requestCount = meter.createCounter('session_search_requests', { description: 'Session Search request count' });
  const requestDuration = meter.createHistogram('session_search_request_duration_ms', { unit: 'ms' });
  const dispatchCount = meter.createCounter('session_search_shard_dispatches', { description: 'Router shard dispatch count' });
  const dispatchDuration = meter.createHistogram('session_search_shard_dispatch_duration_ms', { unit: 'ms' });

  async function forceFlush() {
    const pending = Promise.allSettled([
      tracerProvider.forceFlush({ timeoutMillis: flushTimeoutMs }),
      meterProvider.forceFlush(),
    ]);
    let timeoutId;
    const timeout = new Promise((resolve) => {
      timeoutId = setTimeout(() => resolve(null), flushTimeoutMs);
    });
    const results = await Promise.race([pending, timeout]);
    clearTimeout(timeoutId);
    if (results === null) {
      console.error(JSON.stringify({ event: 'session_search_telemetry_flush_timeout' }));
      return;
    }
    if (results.some((item) => item.status === 'rejected')) {
      console.error(JSON.stringify({ event: 'session_search_telemetry_flush_failed' }));
    }
  }

  async function withRequest(request, route, fn) {
    const routeName = safeRoute(route);
    const parentContext = extractSanitizedParentContext(request);
    const span = tracer.startSpan(`session_search.${routeName}`, {
      kind: SpanKind.SERVER,
      attributes: { 'session_search.route': routeName },
    }, parentContext);
    const requestContext = trace.setSpan(parentContext, span);
    const started = performance.now();
    const state = { errorClass: 'none' };
    const scope = {
      context: requestContext,
      setErrorClass(value) {
        state.errorClass = safeErrorClass(value);
      },
      setResult(value = {}) {
        Object.assign(state, value);
        span.setAttributes(spanResultAttributes(value));
      },
    };
    let response;
    try {
      response = await fn(scope);
      if (response.status >= 500) span.setStatus({ code: SpanStatusCode.ERROR });
      else span.setStatus({ code: SpanStatusCode.OK });
      return response;
    } catch (error) {
      state.errorClass = 'internal';
      span.setStatus({ code: SpanStatusCode.ERROR });
      throw error;
    } finally {
      const status = response?.status ?? 500;
      const attrs = metricAttributes({
        route: routeName,
        status,
        errorClass: state.errorClass,
        resultStatus: state.resultStatus,
        provider: state.provider,
        shardId: state.shardId,
      });
      requestCount.add(1, attrs);
      requestDuration.record(Math.max(0, performance.now() - started), attrs);
      span.setAttributes(spanOutcomeAttributes({ status, errorClass: state.errorClass }));
      span.end();
      await forceFlush();
    }
  }

  async function withDispatch(parentContext, shard, baseHeaders, fn) {
    const provider = safeId(shard?.provider);
    const shardId = safeId(shard?.shard_id);
    const generationId = safeGeneration(shard?.generation_id);
    const attributes = {};
    if (provider) attributes['session_search.provider'] = provider;
    if (shardId) attributes['session_search.shard_id'] = shardId;
    if (generationId) attributes['session_search.generation_id'] = generationId;
    const span = tracer.startSpan('session_search.shard_dispatch', {
      kind: SpanKind.CLIENT,
      attributes,
    }, parentContext ?? ROOT_CONTEXT);
    const dispatchContext = trace.setSpan(parentContext ?? ROOT_CONTEXT, span);
    const headers = new Headers(baseHeaders);
    propagator.inject(dispatchContext, headers, headerSetter);
    const started = performance.now();
    let outcome;
    try {
      outcome = await fn(headers);
      const errorClass = outcome?.ok ? 'none'
        : outcome?.reason === 'TIMEOUT' ? 'timeout'
          : outcome?.reason === 'NOT_READY' ? 'not_ready'
            : 'upstream';
      span.setStatus({ code: outcome?.ok ? SpanStatusCode.OK : SpanStatusCode.ERROR });
      span.setAttributes(spanResultAttributes({
        resultStatus: outcome?.body?.status,
        provider,
        shardId,
        generationId: outcome?.body?.generation_id ?? generationId,
      }));
      const status = outcome?.ok ? 200 : errorClass === 'timeout' ? 504 : 503;
      const attrs = metricAttributes({
        route: 'search',
        status,
        errorClass,
        resultStatus: outcome?.body?.status,
        provider,
        shardId,
      });
      dispatchCount.add(1, attrs);
      dispatchDuration.record(Math.max(0, performance.now() - started), attrs);
      span.setAttributes(spanOutcomeAttributes({ status, errorClass }));
      return outcome;
    } catch (error) {
      span.setStatus({ code: SpanStatusCode.ERROR });
      const attrs = metricAttributes({ route: 'search', status: 503, errorClass: 'upstream', provider, shardId });
      dispatchCount.add(1, attrs);
      dispatchDuration.record(Math.max(0, performance.now() - started), attrs);
      span.setAttributes(spanOutcomeAttributes({ status: 503, errorClass: 'upstream' }));
      throw error;
    } finally {
      span.end();
    }
  }

  return {
    enabled: true,
    withRequest,
    withDispatch,
    forceFlush,
    async shutdown() {
      await Promise.allSettled([tracerProvider.shutdown(), meterProvider.shutdown()]);
    },
  };
}

export function createTelemetryFromEnv({ serviceName, flushTimeoutMs = DEFAULT_FLUSH_TIMEOUT_MS }) {
  const endpoint = process.env.OTEL_EXPORTER_OTLP_ENDPOINT?.replace(/\/+$/, '');
  if (!endpoint) return createNoopTelemetry();
  const headers = parseHeaderEnv(process.env.OTEL_EXPORTER_OTLP_HEADERS);
  const serviceVersion = process.env.SESSION_SEARCH_SERVICE_VERSION ?? 'unknown';
  const environment = process.env.SESSION_SEARCH_ENVIRONMENT ?? 'candidate';
  return createTelemetryRuntime({
    serviceName,
    serviceVersion,
    environment,
    flushTimeoutMs,
    traceExporter: new OTLPTraceExporter({
      url: `${endpoint}/v1/traces`,
      headers,
      timeoutMillis: flushTimeoutMs,
    }),
    metricExporter: new OTLPMetricExporter({
      url: `${endpoint}/v1/metrics`,
      headers,
      timeoutMillis: flushTimeoutMs,
    }),
  });
}
