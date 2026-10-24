const VALID_SCOPES = new Set(['dialogue', 'evidence', 'trace']);

export function buildSearchCall(input = {}) {
  const query = typeof input.query === 'string' ? input.query.trim() : '';
  if (!query || query.length > 512) throw new Error('QUERY_OUT_OF_BOUNDS');

  const scopes = Array.isArray(input.scopes) && input.scopes.length ? input.scopes : ['dialogue', 'evidence'];
  if (!scopes.every((scope) => VALID_SCOPES.has(scope))) throw new Error('SCOPES_OUT_OF_BOUNDS');

  const limit = input.limit ?? 8;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error('LIMIT_OUT_OF_BOUNDS');

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

  return {
    text: 'SELECT session_search.search($1,$2::text[],$3,$4,$5,$6) AS result',
    values: [query, scopes, limit, sessionId, fromTime, toTime],
  };
}
