const VALID_SCOPES = new Set(['dialogue', 'evidence', 'trace']);
const VALID_SEARCH_MODES = new Set(['strict', 'recall', 'auto']);

function validateSearchInput(input = {}) {
  const query = typeof input.query === 'string' ? input.query.trim() : '';
  if (!query || query.length > 512) throw new Error('QUERY_OUT_OF_BOUNDS');
  const scopes = Array.isArray(input.scopes) && input.scopes.length ? input.scopes : ['dialogue', 'evidence'];
  if (!scopes.every((scope) => VALID_SCOPES.has(scope))) throw new Error('SCOPES_OUT_OF_BOUNDS');
  const limit = input.limit ?? 8;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error('LIMIT_OUT_OF_BOUNDS');
  const searchMode = input.search_mode ?? 'strict';
  if (typeof searchMode !== 'string' || !VALID_SEARCH_MODES.has(searchMode)) {
    throw new Error('SEARCH_MODE_OUT_OF_BOUNDS');
  }

  const sessionId = input.session_id ?? null;
  if (sessionId !== null && (typeof sessionId !== 'string' || !sessionId || sessionId.length > 512)) {
    throw new Error('SESSION_OUT_OF_BOUNDS');
  }

  const fromTime = input.from_time ?? null;
  const toTime = input.to_time ?? null;
  for (const value of [fromTime, toTime]) {
    if (value !== null && (typeof value !== 'number' || !Number.isFinite(value))) {
      throw new Error('TIME_OUT_OF_BOUNDS');
    }
  }
  if (fromTime !== null && toTime !== null && fromTime > toTime) {
    throw new Error('TIME_BOUNDS_INVERTED');
  }

  return {
    ...input,
    query,
    scopes,
    limit,
    search_mode: searchMode,
    session_id: sessionId,
    from_time: fromTime,
    to_time: toTime,
  };
}

export function selectShards(catalog, input = {}) {
  return [...catalog]
    .filter((shard) => input.provider == null || shard.provider === input.provider)
    .filter((shard) => {
      if (input.session_id == null || shard.session_ids == null) return true;
      return shard.session_ids.includes(input.session_id);
    })
    .filter((shard) => {
      if (input.from_time != null && shard.time_max != null && shard.time_max < input.from_time) return false;
      if (input.to_time != null && shard.time_min != null && shard.time_min > input.to_time) return false;
      return true;
    })
    .sort((a, b) => a.shard_id.localeCompare(b.shard_id));
}

export function mergeShardOutcomes(selected, outcomes, limit) {
  const queried = [];
  const unavailable = [];
  const ranked = [];

  for (const shard of selected) {
    if (shard.state && shard.state !== 'READY') {
      unavailable.push({ shard_id: shard.shard_id, reason: 'NOT_READY' });
      continue;
    }
    queried.push(shard.shard_id);
    const outcome = outcomes.find((item) => item.shard.shard_id === shard.shard_id);
    if (!outcome || !outcome.ok) {
      unavailable.push({ shard_id: shard.shard_id, reason: outcome?.reason ?? 'QUERY_FAILED' });
      continue;
    }
    if (outcome.body?.generation_id !== shard.generation_id) {
      unavailable.push({ shard_id: shard.shard_id, reason: 'STALE_GENERATION' });
      continue;
    }
    for (let i = 0; i < (outcome.body.hits ?? []).length; i += 1) {
      const original = outcome.body.hits[i];
      const rank = i + 1;
      ranked.push({
        reciprocal: 1 / (60 + rank),
        shard_id: shard.shard_id,
        hit: {
          ...original,
          shard_id: shard.shard_id,
          provider: shard.provider,
          generation_id: shard.generation_id,
          shard_rank: rank,
        },
      });
    }
  }

  ranked.sort((a, b) =>
    b.reciprocal - a.reciprocal ||
    a.shard_id.localeCompare(b.shard_id) ||
    String(a.hit.session_id ?? '').localeCompare(String(b.hit.session_id ?? '')) ||
    Number(a.hit.ordinal ?? 0) - Number(b.hit.ordinal ?? 0) ||
    String(a.hit.message_id ?? '').localeCompare(String(b.hit.message_id ?? ''))
  );
  unavailable.sort((a, b) => a.shard_id.localeCompare(b.shard_id));
  const candidateCount = ranked.length;
  const hits = ranked.slice(0, limit).map((item) => item.hit);
  return {
    status: unavailable.length ? 'PARTIAL' : hits.length ? 'FOUND' : 'NO_SIGNAL',
    hits,
    candidate_count: candidateCount,
    queried_shards: queried,
    unavailable_shards: unavailable,
  };
}

export async function handleMcpRequest(request, searchFn) {
  const id = request?.id ?? null;
  const method = request?.method;

  if (method === 'initialize') {
    return {
      jsonrpc: '2.0',
      id,
      result: {
        protocolVersion: request?.params?.protocolVersion ?? '2025-06-18',
        capabilities: { tools: {} },
        serverInfo: { name: 'theseus-session-search', version: '0.1.0' },
      },
    };
  }
  if (method === 'ping') return { jsonrpc: '2.0', id, result: {} };
  if (method === 'notifications/initialized') return null;
  if (method === 'tools/list') {
    return {
      jsonrpc: '2.0',
      id,
      result: {
        tools: [{
          name: 'session_search',
          description: 'Search federated Session Search shards with provenance and explicit partial-failure semantics.',
          inputSchema: {
            type: 'object',
            additionalProperties: false,
            required: ['query'],
            properties: {
              query: { type: 'string', minLength: 1, maxLength: 512 },
              scopes: { type: 'array', items: { enum: ['dialogue', 'evidence', 'trace'] }, minItems: 1 },
              limit: { type: 'integer', minimum: 1, maximum: 100, default: 8 },
              search_mode: { type: 'string', enum: ['strict', 'recall', 'auto'], default: 'strict' },
              provider: { type: 'string' },
              session_id: { type: 'string' },
              from_time: { type: 'number' },
              to_time: { type: 'number' },
            },
          },
        }],
      },
    };
  }
  if (method === 'tools/call') {
    if (request?.params?.name !== 'session_search') {
      return { jsonrpc: '2.0', id, error: { code: -32602, message: 'unknown tool' } };
    }
    try {
      const input = validateSearchInput(request.params.arguments ?? {});
      const result = await searchFn(input);
      return {
        jsonrpc: '2.0',
        id,
        result: {
          content: [{ type: 'text', text: JSON.stringify(result) }],
          structuredContent: result,
          isError: false,
        },
      };
    } catch (error) {
      return {
        jsonrpc: '2.0',
        id,
        result: {
          content: [{ type: 'text', text: String(error?.message ?? error) }],
          isError: true,
        },
      };
    }
  }
  return { jsonrpc: '2.0', id, error: { code: -32601, message: 'method not found' } };
}

export { validateSearchInput };
