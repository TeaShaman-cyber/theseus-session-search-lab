import { createTelemetryFromEnv } from '../telemetry.mjs';
import { makeRouterHandler } from './handler.mjs';
import { neon } from '@neondatabase/serverless';
import { assertFeedbackDatabaseUrl, makeFeedbackWriter } from './feedback.mjs';

const rawCatalog = process.env.SESSION_SEARCH_CATALOG;
const authToken = process.env.SESSION_SEARCH_ROUTER_AUTH_TOKEN;
const shardAuthToken = process.env.SESSION_SEARCH_SHARD_AUTH_TOKEN;
const resultShape = process.env.SESSION_SEARCH_RESULT_SHAPE ?? 'grouped_v1';

if (!rawCatalog) throw new Error('SESSION_SEARCH_CATALOG_MISSING');
if (!authToken) throw new Error('SESSION_SEARCH_ROUTER_AUTH_TOKEN_MISSING');
if (!shardAuthToken) throw new Error('SESSION_SEARCH_SHARD_AUTH_TOKEN_MISSING');

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

// No server↔App synchronization: the App writes only to its separate issue232 DB.
const feedbackDatabaseUrl = process.env.SESSION_SEARCH_FEEDBACK_DATABASE_URL;
let feedbackWriter = null;
if (feedbackDatabaseUrl) {
  assertFeedbackDatabaseUrl(feedbackDatabaseUrl);
  const sql = neon(feedbackDatabaseUrl);
  feedbackWriter = makeFeedbackWriter(sql.query.bind(sql));
}

export default makeRouterHandler({
  catalog,
  telemetry,
  authToken,
  shardAuthToken,
  feedbackWriter,
  resultShape,
});
