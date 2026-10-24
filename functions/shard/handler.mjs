import { buildSearchCall } from './core.mjs';

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

export function makeShardHandler(executeSearch) {
  return {
    async fetch(request) {
      const url = new URL(request.url);
      if (request.method === 'GET' && url.pathname === '/health') {
        return json({ status: 'OK', service: 'session-search-shard' });
      }
      if (request.method !== 'POST' || url.pathname !== '/search') {
        return json({ error: 'NOT_FOUND' }, 404);
      }
      try {
        const input = await request.json();
        const call = buildSearchCall(input);
        return json(await executeSearch(call));
      } catch (error) {
        const message = String(error?.message ?? error);
        const clientError = message.endsWith('_OUT_OF_BOUNDS') || message === 'TIME_BOUNDS_INVERTED';
        if (!clientError) {
          console.error(JSON.stringify({
            event: 'session_search_shard_query_failed',
            name: String(error?.name ?? 'Error'),
            code: String(error?.code ?? ''),
          }));
        }
        return json({ error: clientError ? message : 'QUERY_FAILED' }, clientError ? 400 : 503);
      }
    },
  };
}
