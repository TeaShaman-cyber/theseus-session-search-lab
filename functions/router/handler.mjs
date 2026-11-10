import { hasValidBearerToken } from '../auth.mjs';
import { createNoopTelemetry } from '../telemetry.mjs';
import {
  handleMcpRequest,
  mergeGroupedShardOutcomes,
  mergeShardOutcomes,
  selectShards,
  validateGroupedCanaryInput,
  validateSearchInput,
} from './core.mjs';

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
  if (request.method === 'POST' && url.pathname === '/internal/grouped-search') return 'grouped_search_canary';
  return 'not_found';
}

export function makeRouterHandler({
  catalog,
  fetchImpl = fetch,
  shardTimeoutMs = 5000,
  telemetry = createNoopTelemetry(),
  authToken,
  shardAuthToken,
  feedbackWriter = null,
  groupedCanaryEnabled = false,
}) {
  if (typeof authToken !== 'string' || authToken.length === 0) {
    throw new Error('HTTP_AUTH_TOKEN_MISSING');
  }
  if (typeof shardAuthToken !== 'string' || shardAuthToken.length === 0) {
    throw new Error('SHARD_AUTH_TOKEN_MISSING');
  }
  if (!Number.isFinite(shardTimeoutMs) || shardTimeoutMs <= 0) {
    throw new Error('SHARD_TIMEOUT_OUT_OF_BOUNDS');
  }
  if (feedbackWriter !== null && typeof feedbackWriter !== 'function') {
    throw new Error('FEEDBACK_WRITER_INVALID');
  }
  if (typeof groupedCanaryEnabled !== 'boolean') {
    throw new Error('GROUPED_CANARY_FLAG_INVALID');
  }

  async function dispatchSelected(input, selected, searchMode, requestContext, { grouped = false } = {}) {
    const outcomes = await Promise.all(selected.map(async (shard) => telemetry.withDispatch(
      requestContext,
      shard,
      { 'content-type': 'application/json', authorization: 'Bearer ' + shardAuthToken },
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
                generation_id: shard.generation_id,
                scopes: input.scopes,
                limit: input.limit,
                search_mode: searchMode,
                session_id: input.session_id ?? null,
                from_time: input.from_time ?? null,
                to_time: input.to_time ?? null,
                ...(grouped ? { grouping: 'exact_text_v1', candidate_budget: input.candidate_budget } : {}),
              }),
              signal: controller.signal,
            });
            if (!response.ok) return { shard, ok: false, reason: 'QUERY_FAILED' };
            const body = await response.json();
            if (body?.generation_binding !== 'router_pinned_v1') {
              return { shard, ok: false, reason: 'PINNING_NOT_CONFIRMED' };
            }
            if (body.shard_id != null && body.shard_id !== shard.shard_id) {
              return { shard, ok: false, reason: 'SHARD_ID_MISMATCH' };
            }
            if (body.provider != null && body.provider !== shard.provider) {
              return { shard, ok: false, reason: 'PROVIDER_MISMATCH' };
            }
            return { shard, ok: true, body };
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

    return grouped
      ? mergeGroupedShardOutcomes(selected, outcomes, input.limit)
      : mergeShardOutcomes(selected, outcomes, input.limit);
  }

  async function searchFederated(rawInput, requestContext, requestRef) {
    const input = validateSearchInput(rawInput);
    const selected = selectShards(catalog, input);

    async function dispatch(searchMode) {
      return dispatchSelected(input, selected, searchMode, requestContext);
    }

    function withReceipt(result, effectiveMode, strictHitCount) {
      const candidateCount = result.candidate_count ?? result.hits?.length ?? 0;
      const { candidate_count: _candidateCount, ...publicResult } = result;
      return {
        ...publicResult,
        request_ref: requestRef,
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

  async function searchFederatedGroupedCanary(rawInput, requestContext, requestRef) {
    const input = validateGroupedCanaryInput(rawInput);
    const selected = selectShards(catalog, input);
    const result = await dispatchSelected(input, selected, 'recall', requestContext, { grouped: true });
    return {
      ...result,
      request_ref: requestRef,
      retrieval: {
        requested_mode: 'recall',
        effective_mode: 'recall',
        candidate_budget: input.candidate_budget,
        candidate_group_count: result.candidate_group_count ?? 0,
        returned_group_count: result.groups?.length ?? 0,
      },
    };
  }

  return {
    async fetch(request) {
      const url = new URL(request.url);
      const groupedCanaryRoute = request.method === 'POST' && url.pathname === '/internal/grouped-search';
      const protectedRoute = request.method === 'POST'
        && (url.pathname === '/search' || url.pathname === '/mcp'
          || (groupedCanaryRoute && groupedCanaryEnabled));
      if (protectedRoute && !hasValidBearerToken(request, authToken)) {
        return json({ error: 'UNAUTHORIZED' }, 401);
      }
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

        if (groupedCanaryRoute) {
          if (!groupedCanaryEnabled) {
            observation.setErrorClass('not_found');
            return json({ error: 'NOT_FOUND' }, 404);
          }
          try {
            const result = await searchFederatedGroupedCanary(
              await request.json(),
              observation.context,
              observation.requestRef,
            );
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

        if (request.method === 'POST' && url.pathname === '/search') {
          try {
            const result = await searchFederated(
              await request.json(),
              observation.context,
              observation.requestRef,
            );
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
          const result = await handleMcpRequest(
            message,
            (input) => searchFederated(input, observation.context, observation.requestRef),
            feedbackWriter,
          );
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
