const VALID_SCOPES = new Set(['dialogue', 'evidence', 'trace']);
const VALID_SEARCH_MODES = new Set(['strict', 'recall']);

export function buildSearchCall(input = {}) {
  const pinned = Object.prototype.hasOwnProperty.call(input, 'generation_id');
  const generationId = input.generation_id;
  if (pinned && (typeof generationId !== 'string' || !/^[0-9a-f]{64}$/.test(generationId))) {
    throw new Error('GENERATION_OUT_OF_BOUNDS');
  }
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
  if (fromTime !== null && toTime !== null && fromTime > toTime) throw new Error('TIME_BOUNDS_INVERTED');

  // The ACTIVE path is temporary compatibility for routers that omit generation_id.
  // Once supplied, generation_id must control the database query without fallback.
  return pinned
    ? {
      text: 'SELECT session_search.search_generation_v2($1,$2,$3::text[],$4,$5,$6,$7,$8) AS result',
      values: [generationId, query, scopes, limit, sessionId, fromTime, toTime, searchMode],
    }
    : {
      text: 'SELECT session_search.search_v2($1,$2::text[],$3,$4,$5,$6,$7) AS result',
      values: [query, scopes, limit, sessionId, fromTime, toTime, searchMode],
    };
}

export function buildGroupedSearchCall(input = {}) {
  const pinned = Object.prototype.hasOwnProperty.call(input, 'generation_id');
  const validated = buildSearchCall(input);
  const resultLimit = pinned ? validated.values[3] : validated.values[2];
  const candidateBudget = input.candidate_budget ?? Math.min(300, resultLimit * 3);
  if (!Number.isInteger(candidateBudget) || candidateBudget < resultLimit || candidateBudget > 300) {
    throw new Error('CANDIDATE_BUDGET_OUT_OF_BOUNDS');
  }

  if (pinned) {
    const [generationId, query, scopes, _limit, sessionId, fromTime, toTime, searchMode] = validated.values;
    return {
      text: 'SELECT session_search.search_generation_grouped_v3($1,$2,$3::text[],$4,$5,$6,$7,$8,$9) AS result',
      values: [
        generationId,
        query,
        scopes,
        resultLimit,
        candidateBudget,
        sessionId,
        fromTime,
        toTime,
        searchMode,
      ],
    };
  }

  const [query, scopes, _limit, sessionId, fromTime, toTime, searchMode] = validated.values;
  return {
    text: 'SELECT session_search.search_grouped_v3($1,$2::text[],$3,$4,$5,$6,$7,$8) AS result',
    values: [
      query,
      scopes,
      resultLimit,
      candidateBudget,
      sessionId,
      fromTime,
      toTime,
      searchMode,
    ],
  };
}

// The browse path is explicitly generation pinned. Never fall back to ACTIVE.
export function buildSessionBrowseCall(input = {}) {
  if (typeof input.generation_id !== 'string' || !/^[0-9a-f]{64}$/.test(input.generation_id)) {
    throw new Error('GENERATION_OUT_OF_BOUNDS');
  }
  if (typeof input.session_id !== 'string' || !input.session_id || input.session_id.length > 512) {
    throw new Error('SESSION_OUT_OF_BOUNDS');
  }
  const ordinal = input.ordinal ?? 0;
  const messageId = input.message_id ?? null;
  const offset = input.offset ?? 0;
  const limit = input.limit ?? 20;
  const maxChars = input.max_chars ?? 16000;
  if (!Number.isSafeInteger(ordinal) || ordinal < 0 || ordinal > 2147483647) {
    throw new Error('ORDINAL_OUT_OF_BOUNDS');
  }
  if (messageId != null && (typeof messageId !== 'string' || !messageId || messageId.length > 512)) {
    throw new Error('MESSAGE_OUT_OF_BOUNDS');
  }
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > 2147483647
      || (offset > 0 && messageId === null)) {
    throw new Error('OFFSET_OUT_OF_BOUNDS');
  }
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new Error('LIMIT_OUT_OF_BOUNDS');
  }
  if (!Number.isInteger(maxChars) || maxChars < 1 || maxChars > 100000) {
    throw new Error('CHAR_BUDGET_OUT_OF_BOUNDS');
  }
  return {
    text: 'SELECT session_search.read_generation_session_v1($1,$2,$3,$4,$5,$6,$7) AS result',
    values: [input.generation_id, input.session_id, ordinal, messageId, offset, limit, maxChars],
  };
}
