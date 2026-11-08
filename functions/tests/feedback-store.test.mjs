import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createUpstashFeedbackWriter,
  createUpstashFeedbackWriterFromEnv,
} from '../feedback/upstash.mjs';

const INPUT = {
  baseline_request_ref: 'a'.repeat(32),
  result_request_ref: 'b'.repeat(32),
  failure_class: 'noisy_query',
  operator: { name: 'light_sonar_rephrase', version: 'v1' },
  outcome: 'improved',
  notes: 'Recovered a narrower result set.',
};

test('feedback writer is disabled when no Upstash feedback env is configured', () => {
  assert.equal(createUpstashFeedbackWriterFromEnv({ env: {} }), null);
});

test('feedback writer fails closed on partial or unsafe configuration', () => {
  assert.throws(
    () => createUpstashFeedbackWriterFromEnv({
      env: { SESSION_SEARCH_FEEDBACK_UPSTASH_REST_URL: 'https://example.upstash.io' },
    }),
    /FEEDBACK_STORE_CONFIG_INCOMPLETE/,
  );
  assert.throws(
    () => createUpstashFeedbackWriter({
      restUrl: 'http://example.upstash.io',
      token: 'secret',
    }),
    /FEEDBACK_STORE_URL_INVALID/,
  );
});

test('Upstash writer records one private feedback record with HSETNX', async () => {
  const calls = [];
  const writer = createUpstashFeedbackWriter({
    restUrl: 'https://example.upstash.io/',
    token: 'secret-token',
    now: () => new Date('2026-11-08T01:20:00.000Z'),
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return Response.json({ result: 1 });
    },
  });

  const result = await writer(INPUT);
  assert.equal(result.status, 'RECORDED');
  assert.match(result.feedback_ref, /^[0-9a-f]{32}$/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://example.upstash.io/');
  const headers = new Headers(calls[0].init.headers);
  assert.equal(headers.get('authorization'), 'Bearer secret-token');
  assert.equal(headers.get('content-type'), 'application/json');

  const command = JSON.parse(calls[0].init.body);
  assert.equal(command[0], 'HSETNX');
  assert.equal(command[1], 'theseus:session-search:feedback:v0');
  assert.equal(command[2], result.feedback_ref);

  const stored = JSON.parse(command[3]);
  assert.equal(stored.schema, 'theseus.session-search-feedback-record.v0');
  assert.equal(stored.feedback_ref, result.feedback_ref);
  assert.equal(stored.received_at, '2026-11-08T01:20:00.000Z');
  assert.deepEqual(stored.feedback, INPUT);
});

test('same feedback payload has stable id and HSETNX duplicate is explicit', async () => {
  const refs = [];
  const writer = createUpstashFeedbackWriter({
    restUrl: 'https://example.upstash.io',
    token: 'secret-token',
    fetchImpl: async (_url, init) => {
      refs.push(JSON.parse(init.body)[2]);
      return Response.json({ result: refs.length === 1 ? 1 : 0 });
    },
  });

  const first = await writer(INPUT);
  const second = await writer({
    outcome: 'improved',
    operator: { version: 'v1', name: 'light_sonar_rephrase' },
    failure_class: 'noisy_query',
    notes: 'Recovered a narrower result set.',
    result_request_ref: 'b'.repeat(32),
    baseline_request_ref: 'a'.repeat(32),
  });

  assert.equal(first.feedback_ref, second.feedback_ref);
  assert.deepEqual(refs, [first.feedback_ref, first.feedback_ref]);
  assert.equal(first.status, 'RECORDED');
  assert.equal(second.status, 'ALREADY_RECORDED');
});

test('Upstash writer redacts provider failures', async () => {
  const writer = createUpstashFeedbackWriter({
    restUrl: 'https://example.upstash.io',
    token: 'super-secret-token',
    fetchImpl: async () => new Response(
      JSON.stringify({ error: 'WRONGPASS super-secret-token' }),
      { status: 401, headers: { 'content-type': 'application/json' } },
    ),
  });

  await assert.rejects(
    () => writer(INPUT),
    (error) => {
      assert.equal(error.message, 'FEEDBACK_STORE_FAILED');
      assert.equal(String(error).includes('super-secret-token'), false);
      assert.equal(String(error).includes('WRONGPASS'), false);
      return true;
    },
  );
});
