import { createNoopTelemetry } from '../telemetry.mjs';
import { handleMcpRequest, mergeShardOutcomes, selectShards, validateSearchInput } from './core.mjs';

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

function routeFor(request, url) {
  if (request.method === 'GET' && url.pathname === '/health') return 'health';
  if (request.method === 'POST' && url.pathname === '/search') return 'search';
  if (request.method === 'POST' && url.pathname === '/mcp') return 'mcp';
  return 'not_found';
}

export function makeRouterHandler({ catalog, fetchImpl = fetch, shardTimeoutMs = 5000, telemetry = createNoopTelemetry() }) {
  if (!Number.isFinite(shardTimeoutMs) || shardTimeoutMs <= 0) {
    throw new Error('SHARD_TIMEOUT_OUT_OF_BOUNDS');
  }
  async function searchFederated(rawInput, requestContext) {
    const input = validateSearchInput(rawInput);
    const selected = selectShards(catalog, input);

    async function dispatch(searchMode) {
      const outcomes = await Promise.all(selected.map(async (shard) => telemetry.withDispatch(
        requestContext,
        shard,
        { 'content-type': 'application/json' },
        async (headers) => {
          if (shard.state && shard.state !== 'READY') {
            return { shard, ok: false, reason: 'NOT_READY' };
          }
          const controller = new AbortController();
          let timeoutId;
          const requestOutcome = (async () => {
            try {
              const response = await fetchImpl(shard.url, {
                method: 'POST',
                headers,
                body: JSON.stringify({
                  query: input.query,
                  scopes: input.scopes,
                  limit: input.limit,
                  search_mode: searchMode,
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
        },
      )));

      return mergeShardOutcomes(selected, outcomes, input.limit);
    }

    function withReceipt(result, effectiveMode, strictHitCount) {
      const candidateCount = result.candidate_count ?? result.hits?.length ?? 0;
      const { candidate_count: _candidateCount, ...publicResult } = result;
      return {
        ...publicResult,
        retrieval: {
          requested_mode: input.search_mode,
          effective_mode: effectiveMode,
          strict_hit_count: strictHitCount,
          candidate_count: candidateCount,
          returned_hit_count: publicResult.hits?.length ?? 0,
        },
      };
    }

    if (input.search_mode !== 'auto') {
      const result = await dispatch(input.search_mode);
      return withReceipt(result, input.search_mode, input.search_mode === 'strict' ? result.hits.length : null);
    }

    const strictResult = await dispatch('strict');
    const strictHitCount = strictResult.hits.length;
    if (strictResult.status !== 'NO_SIGNAL') {
      return withReceipt(strictResult, 'strict', strictHitCount);
    }

    const recallResult = await dispatch('recall');
    return withReceipt(recallResult, 'recall', strictHitCount);
  }

  return {
    async fetch(request) {
      const url = new URL(request.url);
      return telemetry.withRequest(request, routeFor(request, url), async (observation) => {
        if (request.method === 'GET' && url.pathname === '/health') {
          observation.setResult({ resultStatus: 'OK' });
          return json({
            status: 'OK',
            service: 'session-search-router',
            shard_count: catalog.length,
            ready_shards: catalog.filter((item) => !item.state || item.state === 'READY').length,
          });
        }

        if (request.method === 'POST' && url.pathname === '/search') {
          try {
            const result = await searchFederated(await request.json(), observation.context);
            observation.setResult({
              resultStatus: result.status,
              queriedShards: result.queried_shards?.length ?? 0,
              unavailableShards: result.unavailable_shards?.length ?? 0,
            });
            return json(result);
          } catch (error) {
            observation.setErrorClass('validation');
            return json({ error: String(error?.message ?? error) }, 400);
          }
        }

        if (request.method === 'POST' && url.pathname === '/mcp') {
          let message;
          try {
            message = await request.json();
          } catch {
            observation.setErrorClass('validation');
            return json({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'parse error' } }, 400);
          }
          const result = await handleMcpRequest(message, (input) => searchFederated(input, observation.context));
          if (result === null) return new Response(null, { status: 202 });
          const searchResult = result?.result?.structuredContent;
          if (searchResult?.status) {
            observation.setResult({
              resultStatus: searchResult.status,
              queriedShards: searchResult.queried_shards?.length ?? 0,
              unavailableShards: searchResult.unavailable_shards?.length ?? 0,
            });
          }
          return json(result);
        }

        observation.setErrorClass('not_found');
        return json({ error: 'NOT_FOUND' }, 404);
      });
    },
  };
}
