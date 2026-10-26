import { createTelemetryFromEnv } from '../telemetry.mjs';
import { makeRouterHandler } from './handler.mjs';

const rawCatalog = process.env.SESSION_SEARCH_CATALOG;
if (!rawCatalog) throw new Error('SESSION_SEARCH_CATALOG_MISSING');

let catalog;
try {
  catalog = JSON.parse(rawCatalog);
} catch {
  throw new Error('SESSION_SEARCH_CATALOG_INVALID');
}
if (!Array.isArray(catalog) || catalog.length < 1) {
  throw new Error('SESSION_SEARCH_CATALOG_EMPTY');
}

const ROUTER_TELEMETRY_FLUSH_TIMEOUT_MS = 1000;

const telemetry = createTelemetryFromEnv({
  serviceName: 'session-search-router',
  flushTimeoutMs: ROUTER_TELEMETRY_FLUSH_TIMEOUT_MS,
});

export default makeRouterHandler({ catalog, telemetry });
