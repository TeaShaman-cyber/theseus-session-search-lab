import { handleMcpRequest, mergeShardOutcomes, selectShards, validateSearchInput } from './core.mjs';

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

export function makeRouterHandler({ catalog, fetchImpl = fetch, shardTimeoutMs = 5000 }) {
  if (!Number.isFinite(shardTimeoutMs) || shardTimeoutMs <= 0) {
    throw new Error('SHARD_TIMEOUT_OUT_OF_BOUNDS');
  }
  async function searchFederated(rawInput) {
    const input = validateSearchInput(rawInput);
    const selected = selectShards(catalog, input);

    const outcomes = await Promise.all(selected.map(async (shard) => {
      if (shard.state && shard.state !== 'READY') {
        return { shard, ok: false, reason: 'NOT_READY' };
      }
      const controller = new AbortController();
      let timeoutId;
      const requestOutcome = (async () => {
        try {
          const response = await fetchImpl(shard.url, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({
              query: input.query,
              scopes: input.scopes,
              limit: input.limit,
              session_id: input.session_id ?? null,
              from_time: input.from_time ?? null,
              to_time: input.to_time ?? null,
            }),
            signal: controller.signal,
          });
          if (!response.ok) return { shard, ok: false, reason: 'QUERY_FAILED' };
          return { shard, ok: true, body: await response.json() };
        } catch {
          return {
            shard,
            ok: false,
            reason: controller.signal.aborted ? 'TIMEOUT' : 'QUERY_FAILED',
          };
        }
      })();

      const timeoutOutcome = new Promise((resolve) => {
        timeoutId = setTimeout(() => {
          controller.abort();
          resolve({ shard, ok: false, reason: 'TIMEOUT' });
        }, shardTimeoutMs);
      });

      try {
        return await Promise.race([requestOutcome, timeoutOutcome]);
      } finally {
        clearTimeout(timeoutId);
      }
    }));

    return mergeShardOutcomes(selected, outcomes, input.limit);
  }

  return {
    async fetch(request) {
      const url = new URL(request.url);
      if (request.method === 'GET' && url.pathname === '/health') {
        return json({
          status: 'OK',
          service: 'session-search-router',
          shard_count: catalog.length,
          ready_shards: catalog.filter((item) => !item.state || item.state === 'READY').length,
        });
      }

      if (request.method === 'POST' && url.pathname === '/search') {
        try {
          return json(await searchFederated(await request.json()));
        } catch (error) {
          return json({ error: String(error?.message ?? error) }, 400);
        }
      }

      if (request.method === 'POST' && url.pathname === '/mcp') {
        let message;
        try {
          message = await request.json();
        } catch {
          return json({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'parse error' } }, 400);
        }
        const result = await handleMcpRequest(message, searchFederated);
        if (result === null) return new Response(null, { status: 202 });
        return json(result);
      }

      return json({ error: 'NOT_FOUND' }, 404);
    },
  };
}
