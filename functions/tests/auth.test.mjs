import test from 'node:test';
import assert from 'node:assert/strict';

import { makeShardHandler } from '../shard/handler.mjs';
import { makeRouterHandler } from '../router/handler.mjs';

const ROUTER_TOKEN = 'router-test-token';
const SHARD_TOKEN = 'shard-test-token';

test('shard rejects missing and invalid bearer tokens before query execution', async () => {
  let calls = 0;
  const handler = makeShardHandler(async () => {
    calls += 1;
    return { status: 'NO_SIGNAL', hits: [] };
  }, { authToken: SHARD_TOKEN });

  for (const headers of [
    { 'content-type': 'application/json' },
    { 'content-type': 'application/json', authorization: 'Bearer wrong-token' },
  ]) {
    const response = await handler.fetch(new Request('https://shard.test/search', {
      method: 'POST',
      headers,
      body: JSON.stringify({ query: 'canary' }),
    }));
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { error: 'UNAUTHORIZED' });
  }
  assert.equal(calls, 0);
});

test('shard accepts the configured bearer token', async () => {
  let calls = 0;
  const handler = makeShardHandler(async () => {
    calls += 1;
    return { status: 'NO_SIGNAL', hits: [] };
  }, { authToken: SHARD_TOKEN });

  const response = await handler.fetch(new Request('https://shard.test/search', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      authorization: `Bearer ${SHARD_TOKEN}`,
    },
    body: JSON.stringify({ query: 'canary' }),
  }));

  assert.equal(response.status, 200);
  assert.equal(calls, 1);
});

test('router rejects unauthenticated search and MCP calls', async () => {
  const handler = makeRouterHandler({
    catalog: [{ shard_id: 'chatgpt-0001', provider: 'chatgpt', state: 'READY', url: 'https://a.test/search' }],
    fetchImpl: async () => { throw new Error('must not dispatch'); },
    authToken: ROUTER_TOKEN,
    shardAuthToken: SHARD_TOKEN,
  });

  for (const request of [
    new Request('https://router.test/search', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ query: 'canary' }),
    }),
    new Request('https://router.test/search', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: 'Bearer wrong-token' },
      body: JSON.stringify({ query: 'canary' }),
    }),
    new Request('https://router.test/mcp', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
    }),
  ]) {
    const response = await handler.fetch(request);
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { error: 'UNAUTHORIZED' });
  }
});


test('router rejects unauthorized requests before telemetry work', async () => {
  let telemetryCalls = 0;
  const telemetry = {
    enabled: true,
    async withRequest() {
      telemetryCalls += 1;
      throw new Error('telemetry must not run for unauthorized request');
    },
    async withDispatch() {
      throw new Error('dispatch must not run');
    },
  };
  const handler = makeRouterHandler({
    catalog: [{ shard_id: 'chatgpt-0001', provider: 'chatgpt', state: 'READY', url: 'https://a.test/search' }],
    fetchImpl: async () => { throw new Error('must not dispatch'); },
    telemetry,
    authToken: ROUTER_TOKEN,
    shardAuthToken: SHARD_TOKEN,
  });

  const response = await handler.fetch(new Request('https://router.test/mcp', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
  }));

  assert.equal(response.status, 401);
  assert.deepEqual(await response.json(), { error: 'UNAUTHORIZED' });
  assert.equal(telemetryCalls, 0);
});

test('router accepts its bearer token and authenticates shard dispatch separately', async () => {
  let observedAuthorization = null;
  const handler = makeRouterHandler({
    catalog: [{ shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), state: 'READY', url: 'https://a.test/search' }],
    fetchImpl: async (_url, init) => {
      observedAuthorization = new Headers(init.headers).get('authorization');
      return Response.json({ status: 'NO_SIGNAL', generation_id: 'a'.repeat(64), hits: [] });
    },
    authToken: ROUTER_TOKEN,
    shardAuthToken: SHARD_TOKEN,
  });

  const response = await handler.fetch(new Request('https://router.test/search', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      authorization: `Bearer ${ROUTER_TOKEN}`,
    },
    body: JSON.stringify({ query: 'canary' }),
  }));

  assert.equal(response.status, 200);
  assert.equal(observedAuthorization, `Bearer ${SHARD_TOKEN}`);
});


test('handler constructors fail closed when auth configuration is missing', () => {
  assert.throws(
    () => makeShardHandler(async () => ({ status: 'NO_SIGNAL', hits: [] })),
    /HTTP_AUTH_TOKEN_MISSING/,
  );
  assert.throws(
    () => makeRouterHandler({ catalog: [] }),
    /HTTP_AUTH_TOKEN_MISSING/,
  );
});

test('health endpoints remain non-sensitive and public', async () => {
  const shard = makeShardHandler(async () => ({ status: 'NO_SIGNAL', hits: [] }), { authToken: SHARD_TOKEN });
  const router = makeRouterHandler({
    catalog: [{ shard_id: 'chatgpt-0001', provider: 'chatgpt', state: 'READY', url: 'https://a.test/search' }],
    authToken: ROUTER_TOKEN,
    shardAuthToken: SHARD_TOKEN,
  });

  assert.equal((await shard.fetch(new Request('https://shard.test/health'))).status, 200);
  assert.equal((await router.fetch(new Request('https://router.test/health'))).status, 200);
});
