import test from 'node:test';
import assert from 'node:assert/strict';
import { makeFeedbackWriter, assertFeedbackDatabaseUrl } from '../router/feedback.mjs';

const feedback = {
  baseline_request_ref: 'a'.repeat(32),
  result_request_ref: null,
  failure_class: 'noisy_query',
  operator: { name: 'light_sonar_rephrase', version: 'v1' },
  outcome: 'improved',
  notes: null,
};

function fakeSql() {
  const rows = new Map();
  const calls = [];
  return {
    calls, rows,
    async query(text, values) {
      calls.push({ text, values });
      if (text.startsWith('INSERT INTO session_feedback.records_v0')) {
        const ref = values[0];
        if (rows.has(ref)) return [];
        rows.set(ref, JSON.parse(values[1]));
        return [{ feedback_ref: ref }];
      }
      if (text.startsWith('SELECT record FROM session_feedback.records_v0')) {
        const record = rows.get(values[0]);
        return record ? [{ record }] : [];
      }
      throw new Error('unexpected SQL');
    },
  };
}

test('Neon writer uses exact portable feedback_ref and reads back without echoing notes', async () => {
  const sql = fakeSql();
  const writer = makeFeedbackWriter(sql.query.bind(sql), { now: () => '2026-11-08T12:34:56.000Z' });
  const outcome = await writer(feedback);
  assert.deepEqual(outcome, { status: 'RECORDED', feedback_ref: '671d79d7f7c5d8811c2b1547f97b9ea3' });
  assert.equal(sql.rows.size, 1);
  const [record] = [...sql.rows.values()];
  assert.equal(record.schema, 'theseus.session-search-feedback-record.v0');
  assert.equal(record.received_at, '2026-11-08T12:34:56.000Z');
  assert.equal(record.feedback.schema, 'theseus.session-search-feedback-request.v0');
  assert.deepEqual(Object.keys(record.feedback.operator).sort(), ['name', 'version']);
  assert(!JSON.stringify(outcome).includes('noisy_query'));
  assert(sql.calls.every(x => !/UPDATE|DELETE/i.test(x.text)));
});

test('replay is idempotent even if received_at clock changes', async () => {
  const sql = fakeSql();
  const writer = makeFeedbackWriter(sql.query.bind(sql), { now: () => '2026-11-08T12:34:56.000Z' });
  const otherWriter = makeFeedbackWriter(sql.query.bind(sql), { now: () => '2026-11-09T12:34:56.000Z' });
  await writer(feedback);
  const duplicate = await otherWriter(feedback);
  assert.equal(duplicate.status, 'UNCHANGED');
  assert.equal(sql.rows.size, 1);
});

test('conflicting stored feedback with same ID is rejected without overwrite', async () => {
  const sql = fakeSql();
  const writer = makeFeedbackWriter(sql.query.bind(sql));
  await writer(feedback);
  const key = '671d79d7f7c5d8811c2b1547f97b9ea3';
  const before = sql.rows.get(key);
  sql.rows.set(key, { ...before, feedback: { ...before.feedback, outcome: 'regressed' } });
  await assert.rejects(writer(feedback), /FEEDBACK_REF_CONFLICT/);
  assert.equal(sql.rows.get(key).feedback.outcome, 'regressed');
});

test('writer never includes SQL driver details or private notes in errors', async () => {
  const writer = makeFeedbackWriter(async () => { throw new Error('secret password and private notes'); });
  await assert.rejects(writer(feedback), e => e.message === 'FEEDBACK_STORAGE_UNAVAILABLE');
});

test('only explicitly named issue232 database can enable writer', () => {
  assert.equal(assertFeedbackDatabaseUrl('postgresql://user:pw@db.example/feedback_issue232'), true);
  assert.throws(() => assertFeedbackDatabaseUrl('postgresql://user:pw@db.example/neondb'), /FEEDBACK_DATABASE_MISMATCH/);
  assert.throws(() => assertFeedbackDatabaseUrl('not-a-url'), /FEEDBACK_DATABASE_MISMATCH/);
});
