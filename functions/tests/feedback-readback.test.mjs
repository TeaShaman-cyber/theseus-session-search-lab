import test from 'node:test';
import assert from 'node:assert/strict';
import { makeFeedbackReader, makeFeedbackWriter } from '../router/feedback.mjs';
import { handleMcpRequest } from '../router/core.mjs';

const feedback = {
  baseline_request_ref: 'a'.repeat(32),
  result_request_ref: 'b'.repeat(32),
  failure_class: 'low_relevance',
  operator: { name: 'chatgpt', version: 'gpt_6' },
  outcome: 'unknown',
  notes: 'private commentary must stay out of read responses',
};
function fakeSql() {
  const rows = new Map();
  return {
    rows,
    async query(sql, values) {
      if (sql.startsWith('INSERT INTO session_feedback.records_v0')) {
        if (rows.has(values[0])) return [];
        rows.set(values[0], JSON.parse(values[1]));
        return [{ feedback_ref: values[0] }];
      }
      if (sql.startsWith('SELECT record FROM session_feedback.records_v0')) {
        const stored = rows.get(values[0]);
        return stored ? [{ record: stored }] : [];
      }
      throw new Error('unexpected SQL');
    },
  };
}

test('agent reads back an immutable feedback record by exact ref including private notes', async () => {
  const sql = fakeSql();
  const writer = makeFeedbackWriter(sql.query.bind(sql), { now: () => '2026-11-19T16:00:00Z' });
  const reader = makeFeedbackReader(sql.query.bind(sql));
  const write = await writer(feedback);
  assert.equal(write.status, 'RECORDED');
  const result = await reader({ feedback_ref: write.feedback_ref });
  assert.deepEqual(result, {
    status: 'FOUND', feedback_ref: write.feedback_ref, received_at: '2026-11-19T16:00:00Z',
    feedback: {
      baseline_request_ref: 'a'.repeat(32), result_request_ref: 'b'.repeat(32),
      failure_class: 'low_relevance', operator: { name: 'chatgpt', version: 'gpt_6' },
      outcome: 'unknown', notes_present: true, notes: feedback.notes,
    },
  });
  assert.equal(result.feedback.notes, feedback.notes);
  assert.equal((await writer(feedback)).status, 'UNCHANGED');
  assert.deepEqual(await reader({ feedback_ref: write.feedback_ref }), result);
  assert.equal(sql.rows.size, 1);
});

test('readback preserves nullable notes and bounded notes from an existing record', async () => {
  const sql = fakeSql();
  const writer = makeFeedbackWriter(sql.query.bind(sql));
  const reader = makeFeedbackReader(sql.query.bind(sql));
  const nullRef = (await writer({ ...feedback, notes: null })).feedback_ref;
  const nullResult = await reader({ feedback_ref: nullRef });
  assert.equal(nullResult.feedback.notes, null);
  assert.equal(nullResult.feedback.notes_present, false);
  const note = 'A'.repeat(4000);
  const maxRef = (await writer({ ...feedback, notes: note })).feedback_ref;
  const maxResult = await reader({ feedback_ref: maxRef });
  assert.equal(maxResult.feedback.notes, note);
  assert.equal(maxResult.feedback.notes.length, 4000);
});

test('feedback readback detects altered payload even when feedback_ref is unchanged', async () => {
  const sql = fakeSql();
  const writer = makeFeedbackWriter(sql.query.bind(sql));
  const reader = makeFeedbackReader(sql.query.bind(sql));
  const { feedback_ref } = await writer(feedback);
  const row = sql.rows.get(feedback_ref);
  sql.rows.set(feedback_ref, { ...row, feedback: { ...row.feedback, outcome: 'improved' } });
  await assert.rejects(reader({ feedback_ref }), /FEEDBACK_READBACK_INCOMPLETE/);
});

test('agent readback is exact, bounded and fails closed', async () => {
  const sql = fakeSql();
  const reader = makeFeedbackReader(sql.query.bind(sql));
  assert.deepEqual(await reader({ feedback_ref: 'f'.repeat(32) }), {
    status: 'NOT_FOUND', feedback_ref: 'f'.repeat(32),
  });
  await assert.rejects(reader({ feedback_ref: 'abc' }), /FEEDBACK_REF_INVALID/);
  await assert.rejects(reader({ feedback_ref: 'a'.repeat(32), query_text: 'exfiltrate' }), /FEEDBACK_READ_FIELDS_INVALID/);
  await assert.rejects(reader({ feedback_ref: 'A'.repeat(32) }), /FEEDBACK_REF_INVALID/);
  await assert.rejects(reader({}), /FEEDBACK_REF_INVALID/);
  sql.rows.set('c'.repeat(32), { schema: 'invalid', feedback_ref: 'c'.repeat(32), received_at: 'bad', feedback });
  await assert.rejects(reader({ feedback_ref: 'c'.repeat(32) }), /FEEDBACK_READBACK_INCOMPLETE/);
  const failed = makeFeedbackReader(async () => { throw new Error('db password secret'); });
  await assert.rejects(failed({ feedback_ref: 'd'.repeat(32) }), e => e.message === 'FEEDBACK_STORAGE_UNAVAILABLE');
});

test('MCP exposes readback only when explicit reader configured and refuses unknown tools', async () => {
  const request = { jsonrpc: '2.0', id: 1, method: 'tools/list', params: {} };
  const writer = async () => ({ status: 'RECORDED', feedback_ref: 'a'.repeat(32) });
  const reader = async ({ feedback_ref }) => ({ status: 'NOT_FOUND', feedback_ref });
  const search = async () => ({});
  const withoutReader = await handleMcpRequest(request, search, writer);
  assert.deepEqual(withoutReader.result.tools.map(x => x.name), ['session_search', 'session_feedback']);
  const withReader = await handleMcpRequest(request, search, writer, null, reader);
  assert.deepEqual(withReader.result.tools.map(x => x.name), ['session_search', 'session_feedback', 'session_feedback_get']);
  const tool = withReader.result.tools.find(x => x.name === 'session_feedback_get');
  assert.deepEqual(tool.inputSchema.required, ['feedback_ref']);
  assert.equal(tool.inputSchema.additionalProperties, false);
  let called = 0;
  const handlerReader = async ({ feedback_ref }) => { called += 1; return { status: 'NOT_FOUND', feedback_ref }; };
  const args = { name: 'session_feedback_get', arguments: { feedback_ref: 'a'.repeat(32) } };
  const ok = await handleMcpRequest({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: args }, search, writer, null, handlerReader);
  assert.deepEqual(ok.result.structuredContent, { status: 'NOT_FOUND', feedback_ref: 'a'.repeat(32) });
  assert.equal(ok.result.isError, false);
  const invalid = await handleMcpRequest({ jsonrpc: '2.0', id: 4, method: 'tools/call', params: { ...args, arguments: { feedback_ref: 'a'.repeat(32), extra: 'x' } } }, search, writer, null, handlerReader);
  assert.equal(invalid.result.isError, true);
  assert.equal(called, 1);
  const absent = await handleMcpRequest({ jsonrpc: '2.0', id: 5, method: 'tools/call', params: args }, search, writer);
  assert.equal(absent.error.message, 'unknown tool');
});
