// A read-only, generation-pinned conversation walker. Cursor carries position, not authority.
const GENERATION = /^[0-9a-f]{64}$/;
const MAX_SQL_ORDINAL = 2147483647;

function validPosition(p) {
  return p && typeof p === 'object' && !Array.isArray(p)
    && Number.isInteger(p.ordinal) && p.ordinal >= 0 && p.ordinal <= MAX_SQL_ORDINAL
    && (p.message_id === null || (typeof p.message_id === 'string'
      && p.message_id.length > 0 && p.message_id.length <= 512))
    && Number.isInteger(p.offset) && p.offset >= 0 && p.offset <= MAX_SQL_ORDINAL
    && (p.offset === 0 || p.message_id !== null);
}

function decodeCursor(value) {
  if (typeof value !== 'string' || value.length < 1 || value.length > 4096
      || !/^[\w-]+$/.test(value)) throw new Error('CURSOR_INVALID');
  let data;
  try {
    data = JSON.parse(Buffer.from(value, 'base64url').toString('utf8'));
  } catch {
    throw new Error('CURSOR_INVALID');
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)
    || Object.keys(data).sort().join(',') !== 'g,mid,o,offset,s,shard,v'
    || data.v !== 1
    || typeof data.s !== 'string' || typeof data.shard !== 'string'
    || typeof data.g !== 'string'
    || !validPosition({ ordinal: data.o, message_id: data.mid, offset: data.offset })) {
    throw new Error('CURSOR_INVALID');
  }
  return data;
}

function encodeCursor(input, p) {
  return Buffer.from(JSON.stringify({
    v: 1, s: input.session_id, shard: input.shard_id, g: input.generation_id,
    o: p.ordinal, mid: p.message_id, offset: p.offset,
  }), 'utf8').toString('base64url');
}

function comparePosition(a, b) {
  if (a.ordinal !== b.ordinal) return Math.sign(a.ordinal - b.ordinal);
  if (a.message_id === null) return b.message_id === null ? 0 : -1;
  if (b.message_id === null) return 1;
  const idComparison = Buffer.compare(Buffer.from(a.message_id), Buffer.from(b.message_id));
  if (idComparison) return idComparison;
  // offset 0 means completed message; any non-zero offset lies *within* it.
  if (a.offset === 0) return b.offset === 0 ? 0 : 1;
  if (b.offset === 0) return -1;
  return Math.sign(a.offset - b.offset);
}

export function validateSessionBrowseInput(input = {}) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('BROWSE_INPUT_INVALID');
  }
  const allowed = new Set(['session_id', 'shard_id', 'generation_id',
    'cursor', 'start_ordinal', 'limit', 'max_chars']);
  if (Object.keys(input).some((field) => !allowed.has(field))) {
    throw new Error('BROWSE_INPUT_INVALID');
  }
  if (typeof input.session_id !== 'string' || !input.session_id || input.session_id.length > 512) {
    throw new Error('SESSION_OUT_OF_BOUNDS');
  }
  if (typeof input.shard_id !== 'string' || !input.shard_id || input.shard_id.length > 256) {
    throw new Error('SHARD_OUT_OF_BOUNDS');
  }
  if (typeof input.generation_id !== 'string' || !GENERATION.test(input.generation_id)) {
    throw new Error('GENERATION_OUT_OF_BOUNDS');
  }
  const limit = input.limit ?? 20;
  const maxChars = input.max_chars ?? 16000;
  const startOrdinal = input.start_ordinal ?? 0;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error('LIMIT_OUT_OF_BOUNDS');
  if (!Number.isInteger(maxChars) || maxChars < 1 || maxChars > 100000) {
    throw new Error('CHAR_BUDGET_OUT_OF_BOUNDS');
  }
  if (!Number.isInteger(startOrdinal) || startOrdinal < 0 || startOrdinal > MAX_SQL_ORDINAL) {
    throw new Error('ORDINAL_OUT_OF_BOUNDS');
  }
  if (input.cursor != null && input.start_ordinal != null) {
    throw new Error('CURSOR_AND_START_CONFLICT');
  }
  const cursor = input.cursor == null ? null : decodeCursor(input.cursor);
  if (cursor && cursor.s !== input.session_id) throw new Error('CURSOR_SESSION_MISMATCH');
  if (cursor && cursor.shard !== input.shard_id) throw new Error('CURSOR_SHARD_MISMATCH');
  if (cursor && cursor.g !== input.generation_id) throw new Error('STALE_GENERATION');
  return {
    session_id: input.session_id,
    shard_id: input.shard_id,
    generation_id: input.generation_id,
    limit,
    max_chars: maxChars,
    cursor: input.cursor ?? null,
    position: cursor
      ? { ordinal: cursor.o, message_id: cursor.mid, offset: cursor.offset }
      : { ordinal: startOrdinal, message_id: null, offset: 0 },
  };
}

function blocked(input, reason, queried = []) {
  return {
    status: 'BLOCKED',
    session_id: input.session_id,
    shard_id: input.shard_id,
    generation_id: input.generation_id,
    messages: [],
    has_more: null,
    next_cursor: input.cursor,
    coverage_complete: false,
    queried_shards: queried,
    unavailable_shards: [{ shard_id: input.shard_id, reason }],
  };
}

function confirmedPayload(input, shard, body) {
  if (body?.generation_binding !== 'router_pinned_v1') return 'PINNING_NOT_CONFIRMED';
  if (body.generation_id !== input.generation_id) return 'STALE_GENERATION';
  if (body.shard_id !== shard.shard_id) return 'SHARD_ID_MISMATCH';
  if (body.provider !== shard.provider) return 'PROVIDER_MISMATCH';
  if (body.session_id !== input.session_id) return 'SESSION_ID_MISMATCH';
  if (body.status !== 'OK' || typeof body.has_more !== 'boolean'
    || typeof body.session_coverage !== 'string' || !body.session_coverage
    || !Array.isArray(body.messages) || body.messages.length > input.limit) {
    return 'BROWSE_PAYLOAD_INVALID';
  }
  let previous = input.position;
  let totalChars = 0;
  let previousIncomplete = false;
  let first = true;
  for (const message of body.messages) {
    if (previousIncomplete) return 'BROWSE_PAYLOAD_INVALID';
    if (!message || typeof message !== 'object'
        || !validPosition({ ordinal: message.ordinal, message_id: message.message_id,
          offset: message.text_offset })
        || typeof message.text !== 'string'
        || !Number.isInteger(message.text_total_chars)
        || message.text_total_chars < 0
        || typeof message.complete_message !== 'boolean') {
      return 'BROWSE_PAYLOAD_INVALID';
    }
    const textChars = Array.from(message.text).length;
    totalChars += textChars;
    const expectedOffset = first && input.position.message_id === message.message_id
      && input.position.ordinal === message.ordinal ? input.position.offset : 0;
    const actuallyComplete = message.text_offset + textChars === message.text_total_chars;
    if (message.text_offset !== expectedOffset || message.complete_message !== actuallyComplete) {
      return 'BROWSE_PAYLOAD_INVALID';
    }
    if (comparePosition({ ordinal: message.ordinal, message_id: message.message_id,
      offset: message.complete_message ? 0 : message.text_offset + textChars }, previous) <= 0) {
      return 'NONADVANCING_CURSOR';
    }
    previous = { ordinal: message.ordinal, message_id: message.message_id,
      offset: message.complete_message ? 0 : message.text_offset + textChars };
    previousIncomplete = !message.complete_message;
    first = false;
  }
  if (totalChars > input.max_chars || (!body.has_more && previousIncomplete)) {
    return 'BROWSE_PAYLOAD_INVALID';
  }
  if (body.has_more) {
    if (!body.messages.length || !validPosition(body.next_position)
        || comparePosition(body.next_position, input.position) <= 0
        || comparePosition(body.next_position, previous) !== 0) return 'NONADVANCING_CURSOR';
  } else if (body.next_position != null) {
    return 'BROWSE_PAYLOAD_INVALID';
  }
  return null;
}

export function makeSessionBrowser({ catalog, fetchImpl, shardAuthToken, shardTimeoutMs, telemetry }) {
  return async (rawInput, requestContext) => {
    const input = validateSessionBrowseInput(rawInput);
    const matches = catalog.filter((shard) => shard.shard_id === input.shard_id);
    if (!matches.length) return blocked(input, 'SHARD_UNAVAILABLE');
    if (matches.length !== 1) return blocked(input, 'SHARD_AMBIGUOUS');
    const shard = matches[0];
    if (shard.generation_id !== input.generation_id) return blocked(input, 'STALE_GENERATION');
    if (shard.state && shard.state !== 'READY') return blocked(input, 'NOT_READY');
    let url;
    try {
      url = new URL(shard.url);
      if (!url.pathname.endsWith('/search')) return blocked(input, 'BROWSE_ROUTE_UNAVAILABLE');
      url.pathname = url.pathname.replace(/\/search$/, '/session-read');
    } catch {
      return blocked(input, 'BROWSE_ROUTE_UNAVAILABLE');
    }
    return telemetry.withDispatch(
      requestContext,
      shard,
      { 'content-type': 'application/json', authorization: 'Bearer ' + shardAuthToken },
      async (headers) => {
        const controller = new AbortController();
        let timer;
        const network = (async () => {
          try {
            const response = await fetchImpl(url.toString(), {
              method: 'POST', headers,
              body: JSON.stringify({
                generation_id: input.generation_id,
                session_id: input.session_id,
                ordinal: input.position.ordinal,
                message_id: input.position.message_id,
                offset: input.position.offset,
                limit: input.limit,
                max_chars: input.max_chars,
              }),
              signal: controller.signal,
            });
            if (!response.ok) return blocked(input, 'QUERY_FAILED', [shard.shard_id]);
            const body = await response.json();
            const issue = confirmedPayload(input, shard, body);
            if (issue) return blocked(input, issue, [shard.shard_id]);
            return {
              ...body,
              next_cursor: body.has_more ? encodeCursor(input, body.next_position) : null,
              coverage_complete: !body.has_more && body.session_coverage === 'COMPLETE',
              queried_shards: [shard.shard_id],
              unavailable_shards: [],
            };
          } catch {
            return blocked(input, controller.signal.aborted ? 'TIMEOUT' : 'QUERY_FAILED', [shard.shard_id]);
          }
        })();
        const timeout = new Promise((resolve) => {
          timer = setTimeout(() => {
            controller.abort();
            resolve(blocked(input, 'TIMEOUT', [shard.shard_id]));
          }, shardTimeoutMs);
        });
        try {
          return await Promise.race([network, timeout]);
        } finally {
          clearTimeout(timer);
        }
      },
    );
  };
}
