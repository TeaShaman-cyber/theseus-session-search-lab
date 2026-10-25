import { createNoopTelemetry } from '../telemetry.mjs';
import { buildSearchCall } from './core.mjs';

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

function routeFor(request, url) {
  if (request.method === 'GET' && url.pathname === '/health') return 'health';
  if (request.method === 'POST' && url.pathname === '/search') return 'search';
  return 'not_found';
}

export function makeShardHandler(executeSearch, { telemetry = createNoopTelemetry() } = {}) {
  return {
    async fetch(request) {
      const url = new URL(request.url);
      return telemetry.withRequest(request, routeFor(request, url), async (observation) => {
        if (request.method === 'GET' && url.pathname === '/health') {
          observation.setResult({ resultStatus: 'OK' });
          return json({ status: 'OK', service: 'session-search-shard' });
        }
        if (request.method !== 'POST' || url.pathname !== '/search') {
          observation.setErrorClass('not_found');
          return json({ error: 'NOT_FOUND' }, 404);
        }
        try {
          const input = await request.json();
          const call = buildSearchCall(input);
          const result = await executeSearch(call);
          observation.setResult({
            resultStatus: result?.status,
            provider: result?.provider,
            shardId: result?.shard_id,
            generationId: result?.generation_id,
          });
          return json(result);
        } catch (error) {
          const message = String(error?.message ?? error);
          const clientError = message.endsWith('_OUT_OF_BOUNDS') || message === 'TIME_BOUNDS_INVERTED';
          observation.setErrorClass(clientError ? 'validation' : 'upstream');
          if (!clientError) {
            console.error(JSON.stringify({
              event: 'session_search_shard_query_failed',
              name: String(error?.name ?? 'Error'),
              code: String(error?.code ?? ''),
            }));
          }
          return json({ error: clientError ? message : 'QUERY_FAILED' }, clientError ? 400 : 503);
        }
      });
    },
  };
}
