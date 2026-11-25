import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSessionBrowseCall } from '../shard/core.mjs';
import { makeShardHandler } from '../shard/handler.mjs';
import { makeRouterHandler } from '../router/handler.mjs';

const A = 'a'.repeat(64);
const B = 'b'.repeat(64);
const catalog = (generation = A) => [{
  shard_id: 'chatgpt-0001', provider: 'chatgpt', state: 'READY',
  generation_id: generation, url: 'https://shard.test/search',
}];
const auth = { authToken: 'router', shardAuthToken: 'shard' };
const request = (name, args) => new Request('https://router.test/mcp', {
  method: 'POST', headers: { authorization: 'Bearer router', 'content-type': 'application/json' },
  body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name, arguments: args } }),
});
const browseInput = (overrides = {}) => ({
  session_id: 'branch:1', shard_id: 'chatgpt-0001',
  generation_id: A, limit: 2, max_chars: 4, ...overrides,
});
const page = ({ messages, has_more, next_position, generation = A } = {}) => ({
  status: 'OK', session_id: 'branch:1', shard_id: 'chatgpt-0001',
  provider: 'chatgpt', generation_id: generation,
  session_title: 'Branch 1', session_coverage: 'PARTIAL_SESSION_SLICE',
  messages: messages ?? [], has_more: has_more ?? false,
  next_position: next_position ?? null,
});

async function toolCall(router, args) {
  const response = await router.fetch(request('session_browse', args));
  assert.equal(response.status, 200);
  return (await response.json()).result;
}

test('cloud session browse requires explicitly pinned generation and session, without search query', () => {
  const sql = buildSessionBrowseCall({
    generation_id: A, session_id: 'branch:1', ordinal: 5,
    message_id: 'm5', offset: 2, limit: 2, max_chars: 4,
  });
  assert.match(sql.text, /session_search\.read_generation_session_v1/);
  assert.deepEqual(sql.values, [A, 'branch:1', 5, 'm5', 2, 2, 4]);
  for (const value of [undefined, null, B.slice(1), 'z'.repeat(64)]) {
    assert.throws(() => buildSessionBrowseCall({
      generation_id: value, session_id: 'branch:1',
    }), /GENERATION_OUT_OF_BOUNDS/);
  }
});

test('shard session-read route uses exact pinned SQL and preserves generation witness', async () => {
  const calls = [];
  const shard = makeShardHandler(async (call) => {
    calls.push(call);
    return page({ messages: [{ ordinal: 0, message_id: 'm1', text: 'hey',
      text_offset: 0, text_total_chars: 3, complete_message: true }] });
  }, { authToken: 'shard' });
  const body = { generation_id: A, session_id: 'branch:1', ordinal: 0,
    message_id: null, offset: 0, limit: 2, max_chars: 4 };
  const resp = await shard.fetch(new Request('https://shard.test/session-read', {
    method: 'POST', headers: { authorization: 'Bearer shard' },
    body: JSON.stringify(body),
  }));
  assert.equal(resp.status, 200);
  const result = await resp.json();
  assert.equal(calls.length, 1);
  assert.match(calls[0].text, /session_search\.read_generation_session_v1/);
  assert.equal(result.generation_binding, 'router_pinned_v1');
  assert.equal(result.generation_id, A);
});

test('router exposes session_browse MCP tool and reconstructs pages without deduplicating identical text', async () => {
  const texts = ['same', 'same', '', '🍵ok', 'done'];
  const messages = texts.map((text, ordinal) => ({
    ordinal, message_id: 'm' + ordinal, role: 'user', search_class: 'dialogue',
    text, text_offset: 0, text_total_chars: Array.from(text).length, complete_message: true,
  }));
  const seen = [];
  const shard = makeShardHandler(async (call) => {
    seen.push(call.values);
    const [_gen, _session, ordinal, previous, offset, limit] = call.values;
    assert.equal(offset, 0);
    const start = previous == null ? ordinal : ordinal + 1;
    const items = messages.slice(start, start + limit);
    const last = items.at(-1);
    const has_more = start + limit < messages.length;
    return page({ messages: items, has_more,
      next_position: has_more ? { ordinal: last.ordinal, message_id: last.message_id, offset: 0 } : null });
  }, { authToken: 'shard' });
  const router = makeRouterHandler({
    catalog: catalog(), ...auth,
    fetchImpl: (_url, init) => shard.fetch(new Request('https://shard.test/session-read', init)),
  });
  const listing = await router.fetch(new Request('https://router.test/mcp', {
    method: 'POST', headers: { authorization: 'Bearer router' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 7, method: 'tools/list' }),
  }));
  const tools = (await listing.json()).result.tools;
  assert.ok(tools.some((tool) => tool.name === 'session_browse'));

  let cursor = null;
  const found = [];
  for (let i = 0; i < 6; i++) {
    const response = await toolCall(router, browseInput({ limit: 1, ...(cursor ? { cursor } : {}) }));
    assert.equal(response.isError, false);
    const data = response.structuredContent;
    assert.equal(data.status, 'OK');
    assert.equal(data.session_coverage, 'PARTIAL_SESSION_SLICE');
    found.push(...data.messages);
    if (!data.has_more) {
      assert.equal(data.next_cursor, null);
      break;
    }
    assert.ok(data.next_cursor && data.next_cursor !== cursor);
    cursor = data.next_cursor;
  }
  assert.deepEqual(found.map((m) => m.text), texts);
  assert.deepEqual(found.map((m) => m.message_id), messages.map((m) => m.message_id));
  assert.equal(seen.length, 5);
});

test('new generation rejects old cursor instead of silently reading new projection', async () => {
  const first = page({ messages: [{
    ordinal: 0, message_id: 'm0', text: 'a', text_offset: 0,
    text_total_chars: 1, complete_message: true,
  }], has_more: true, next_position: { ordinal: 0, message_id: 'm0', offset: 0 } });
  const shard = makeShardHandler(async () => first, { authToken: 'shard' });
  const fetchImpl = (_url, init) => shard.fetch(new Request('https://shard.test/session-read', init));
  const oldRouter = makeRouterHandler({ catalog: catalog(A), ...auth, fetchImpl });
  const firstResult = (await toolCall(oldRouter, browseInput())).structuredContent;
  assert.ok(firstResult.next_cursor);
  const newRouter = makeRouterHandler({ catalog: catalog(B), ...auth, fetchImpl });
  const stale = (await toolCall(newRouter, browseInput({ cursor: firstResult.next_cursor }))).structuredContent;
  assert.equal(stale.status, 'BLOCKED');
  assert.deepEqual(stale.unavailable_shards, [{ shard_id: 'chatgpt-0001', reason: 'STALE_GENERATION' }]);
});

test('missing shard and transport failure remain BLOCKED instead of empty complete history', async () => {
  const missing = makeRouterHandler({ catalog: [], ...auth });
  const notFound = (await toolCall(missing, browseInput())).structuredContent;
  assert.equal(notFound.status, 'BLOCKED');
  assert.equal(notFound.coverage_complete, false);
  const failing = makeRouterHandler({
    catalog: catalog(A), ...auth, fetchImpl: async () => Response.json({ error: 'QUERY_FAILED' }, { status: 503 }),
  });
  const unavailable = (await toolCall(failing, browseInput())).structuredContent;
  assert.equal(unavailable.status, 'BLOCKED');
  assert.equal(unavailable.coverage_complete, false);
  assert.equal(unavailable.unavailable_shards[0].reason, 'QUERY_FAILED');
});

test('cursor resumes inside a Unicode message and preserves another event at same ordinal', async () => {
  const shard = makeShardHandler(async (call) => {
    const [_gen, _sid, ord, mid, offset] = call.values;
    if (mid === null) {
      assert.equal(ord, 0);
      return page({
        messages: [{ ordinal: 0, message_id: 'id-a', text: '🍵', text_offset: 0,
          text_total_chars: 3, complete_message: false }],
        has_more: true, next_position: { ordinal: 0, message_id: 'id-a', offset: 1 },
      });
    }
    if (mid === 'id-a' && offset === 1) {
      return page({
        messages: [{ ordinal: 0, message_id: 'id-a', text: 'βγ', text_offset: 1,
          text_total_chars: 3, complete_message: true }],
        has_more: true, next_position: { ordinal: 0, message_id: 'id-a', offset: 0 },
      });
    }
    assert.equal(mid, 'id-a');
    assert.equal(offset, 0);
    return page({
      messages: [{ ordinal: 0, message_id: 'id-b', text: '', text_offset: 0,
        text_total_chars: 0, complete_message: true }],
      has_more: false,
    });
  }, { authToken: 'shard' });
  const r = makeRouterHandler({
    catalog: catalog(), ...auth,
    fetchImpl: (_url, init) => shard.fetch(new Request('https://shard.test/session-read', init)),
  });
  const result = [];
  let cursor = null;
  for (let i = 0; i < 3; i++) {
    const response = (await toolCall(r, browseInput({
      limit: 1, max_chars: 4, ...(cursor ? { cursor } : {}),
    }))).structuredContent;
    assert.equal(response.status, 'OK');
    result.push(response.messages[0]);
    cursor = response.next_cursor;
  }
  assert.equal(result.map((m) => m.text).join(''), '🍵βγ');
  assert.deepEqual(result.map((m) => m.message_id), ['id-a', 'id-a', 'id-b']);
  assert.equal(cursor, null);
});

test('invalid cursor shapes fail closed before network dispatch', async () => {
  let dispatched = 0;
  const r = makeRouterHandler({
    catalog: catalog(), ...auth,
    fetchImpl: async () => { dispatched++; throw new Error('unexpected network'); },
  });
  for (const cursor of ['!', '', 'e30', 'bnVsbA', 'ZGVm']) {
    const response = await toolCall(r, browseInput({ cursor }));
    assert.equal(response.isError, true, JSON.stringify(cursor));
    assert.match(response.content[0].text, /CURSOR_INVALID/);
  }
  assert.equal(dispatched, 0);
});

test('broken shard continuation and mismatched identity never imply completion', async () => {
  for (const [body, expected] of [
    [page({ messages: [{ ordinal: 0, message_id: 'm', text: 'x', text_offset: 0,
      text_total_chars: 2, complete_message: false }],
    has_more: true, next_position: { ordinal: 0, message_id: 'm', offset: 0 } }), 'NONADVANCING_CURSOR'],
    [page({ messages: [], has_more: true,
      next_position: { ordinal: 0, message_id: 'm', offset: 0 } }), 'NONADVANCING_CURSOR'],
    [page({ generation: B }), 'STALE_GENERATION'],
    [{ ...page(), session_id: 'different-session' }, 'SESSION_ID_MISMATCH'],
  ]) {
    const r = makeRouterHandler({
      catalog: catalog(), ...auth,
      fetchImpl: async () => Response.json({ ...body, generation_binding: 'router_pinned_v1' }),
    });
    const result = (await toolCall(r, browseInput())).structuredContent;
    assert.equal(result.status, 'BLOCKED');
    assert.equal(result.coverage_complete, false);
    assert.equal(result.unavailable_shards[0].reason, expected);
  }
});

test('failure on page 2 returns the failed cursor without erasing page 1', async () => {
  let calls = 0;
  const first = page({
    messages: [{ ordinal: 0, message_id: 'm0', text: 'ok',
      text_offset: 0, text_total_chars: 2, complete_message: true }],
    has_more: true, next_position: { ordinal: 0, message_id: 'm0', offset: 0 },
  });
  const router = makeRouterHandler({ catalog: catalog(), ...auth,
    fetchImpl: async () => {
      calls++;
      return calls === 1
        ? Response.json({ ...first, generation_binding: 'router_pinned_v1' })
        : Response.json({ error: 'QUERY_FAILED' }, { status: 503 });
    },
  });
  const saved = (await toolCall(router, browseInput())).structuredContent;
  assert.equal(saved.status, 'OK');
  assert.equal(saved.messages[0].text, 'ok');
  const failed = (await toolCall(router, browseInput({ cursor: saved.next_cursor }))).structuredContent;
  assert.equal(failed.status, 'BLOCKED');
  assert.equal(failed.next_cursor, saved.next_cursor);
  assert.equal(failed.has_more, null);
  assert.deepEqual(failed.queried_shards, ['chatgpt-0001']);
});

test('a shard cannot silently omit the prefix or claim an unfinished message is complete', async () => {
  const validMessage = {
    ordinal: 0, message_id: 'm0', role: 'user', search_class: 'dialogue',
    text: 'abc', text_offset: 0, text_total_chars: 3, complete_message: true,
  };
  for (const caseMessage of [
    { ...validMessage, text: 'bc', text_offset: 1 }, // omitted 'a'
    { ...validMessage, complete_message: false }, // cannot finish page at end of projection
  ]) {
    const router = makeRouterHandler({
      catalog: catalog(), ...auth,
      fetchImpl: async () => Response.json({
        ...page({ messages: [caseMessage], has_more: false }),
        generation_binding: 'router_pinned_v1',
      }),
    });
    const result = (await toolCall(router, browseInput())).structuredContent;
    assert.equal(result.status, 'BLOCKED');
    assert.equal(result.unavailable_shards[0].reason, 'BROWSE_PAYLOAD_INVALID');
  }
});

test('a shard cannot skip a tail by returning another message after an incomplete segment', async () => {
  const router = makeRouterHandler({
    catalog: catalog(), ...auth,
    fetchImpl: async () => Response.json({
      ...page({
        messages: [
          { ordinal: 0, message_id: 'm0', text: 'ab', text_offset: 0,
            text_total_chars: 10, complete_message: false },
          { ordinal: 1, message_id: 'm1', text: 'cd', text_offset: 0,
            text_total_chars: 2, complete_message: true },
        ],
        has_more: true, next_position: { ordinal: 1, message_id: 'm1', offset: 0 },
      }),
      generation_binding: 'router_pinned_v1',
    }),
  });
  const result = (await toolCall(router, browseInput())).structuredContent;
  assert.equal(result.status, 'BLOCKED');
  assert.equal(result.unavailable_shards[0].reason, 'BROWSE_PAYLOAD_INVALID');
});

test('canonical complete exposed conversation marks coverage only at final page', async () => {
  let calls = 0;
  const shard = makeShardHandler(async (sql) => {
    calls += 1;
    const start = sql.values[3] === null ? 0 : 1;
    const messages = [
      { ordinal: 0, message_id: 'one', role: 'user', search_class: 'dialogue',
        text: 'first', text_offset: 0, text_total_chars: 5, complete_message: true },
      { ordinal: 1, message_id: 'two', role: 'assistant', search_class: 'dialogue',
        text: 'second', text_offset: 0, text_total_chars: 6, complete_message: true },
    ];
    return {
      ...page({ messages: [messages[start]], has_more: start === 0,
        next_position: start === 0 ? { ordinal: 0, message_id: 'one', offset: 0 } : null }),
      session_coverage: 'COMPLETE_EXPOSED_CONVERSATION',
    };
  }, { authToken: 'shard' });
  const router = makeRouterHandler({
    catalog: catalog(), ...auth,
    fetchImpl: (_url, init) => shard.fetch(new Request('https://shard.test/session-read', init)),
  });
  const first = (await toolCall(router, browseInput({ max_chars: 10, limit: 1 }))).structuredContent;
  assert.equal(first.status, 'OK');
  assert.equal(first.coverage_complete, false);
  assert.equal(first.has_more, true);
  const last = (await toolCall(router, browseInput({
    max_chars: 10, limit: 1, cursor: first.next_cursor,
  }))).structuredContent;
  assert.equal(last.status, 'OK');
  assert.equal(last.has_more, false);
  assert.equal(last.session_coverage, 'COMPLETE_EXPOSED_CONVERSATION');
  assert.equal(last.coverage_complete, true);
  assert.equal(calls, 2);
});

const workerReadToken = 'synthetic-worker-read';
const nativeBrowse = (router, args, token = workerReadToken) => router.fetch(new Request('https://router.test/session-browse', {
  method: 'POST',
  headers: token === null ? {} : { authorization: 'Bearer ' + token },
  body: JSON.stringify(args),
}));

test('native conversation read is disabled unless a separate scoped token is configured', async () => {
  const disabled = makeRouterHandler({ catalog: catalog(), ...auth });
  assert.equal((await nativeBrowse(disabled, browseInput())).status, 404);
  for (const token of [auth.authToken, auth.shardAuthToken, '', 'with space']) {
    assert.throws(() => makeRouterHandler({
      catalog: catalog(), ...auth, sessionBrowseAuthToken: token,
    }), /SESSION_BROWSE_AUTH_TOKEN_INVALID/);
  }
});

test('native browse token cannot use other protected routes and rejects wrong tokens before dispatch', async () => {
  let dispatched = 0;
  const router = makeRouterHandler({
    catalog: catalog(), ...auth, sessionBrowseAuthToken: workerReadToken,
    groupedCanaryEnabled: true,
    fetchImpl: async () => { dispatched++; return Response.json({}); },
  });
  for (const token of [null, 'wrong', auth.authToken, auth.shardAuthToken]) {
    assert.equal((await nativeBrowse(router, browseInput(), token)).status, 401);
  }
  for (const route of ['/search', '/coverage', '/mcp', '/internal/grouped-search']) {
    const res = await router.fetch(new Request('https://router.test' + route, {
      method: 'POST', headers: { authorization: 'Bearer ' + workerReadToken },
      body: JSON.stringify({}),
    }));
    assert.equal(res.status, 401);
  }
  assert.equal(dispatched, 0);
});

test('native browse reuses exact MCP result and rejects stale generation before shard dispatch', async () => {
  let dispatched = 0;
  const shard = makeShardHandler(async () => page({
    messages: [{ ordinal: 0, message_id: 'm0', role: 'user', search_class: 'dialogue',
      text: '🍵', text_offset: 0, text_total_chars: 1, complete_message: true }],
  }), { authToken: 'shard' });
  const config = {
    catalog: catalog(), ...auth, sessionBrowseAuthToken: workerReadToken,
    fetchImpl: (_url, init) => {
      dispatched++;
      return shard.fetch(new Request('https://shard.test/session-read', init));
    },
  };
  const router = makeRouterHandler(config);
  const native = await nativeBrowse(router, browseInput());
  assert.equal(native.status, 200);
  assert.deepEqual(await native.json(), (await toolCall(router, browseInput())).structuredContent);
  assert.equal(dispatched, 2);
  const current = makeRouterHandler({ ...config, catalog: catalog(B) });
  const stale = await nativeBrowse(current, browseInput());
  assert.equal(stale.status, 200);
  assert.deepEqual((await stale.json()).unavailable_shards, [{ shard_id: 'chatgpt-0001', reason: 'STALE_GENERATION' }]);
  assert.equal(dispatched, 2);
  const invalid = await nativeBrowse(router, browseInput({ limit: 0 }));
  assert.equal(invalid.status, 400);
  assert.equal(dispatched, 2);
});

test('native browse preserves unavailable shard rather than empty complete history', async () => {
  const router = makeRouterHandler({
    catalog: catalog(), ...auth, sessionBrowseAuthToken: workerReadToken,
    fetchImpl: async () => new Response('', { status: 503 }),
  });
  const res = await nativeBrowse(router, browseInput());
  assert.equal(res.status, 200);
  const result = await res.json();
  assert.equal(result.status, 'BLOCKED');
  assert.equal(result.coverage_complete, false);
  assert.equal(result.unavailable_shards[0].reason, 'QUERY_FAILED');
});
