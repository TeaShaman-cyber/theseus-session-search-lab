import { createHash } from 'node:crypto';
import { validateFeedbackInput } from './core.mjs';

const REQUEST_SCHEMA = 'theseus.session-search-feedback-request.v0';
const RECORD_SCHEMA = 'theseus.session-search-feedback-record.v0';
const TABLE = 'session_feedback.records_v0';

export function assertFeedbackDatabaseUrl(value) {
  try {
    const parsed = new URL(value);
    if (!['postgresql:', 'postgres:'].includes(parsed.protocol)
      || decodeURIComponent(parsed.pathname) !== '/feedback_issue232'
      || !parsed.hostname || !parsed.username) {
      throw new Error('FEEDBACK_DATABASE_MISMATCH');
    }
  } catch {
    throw new Error('FEEDBACK_DATABASE_MISMATCH');
  }
  return true;
}

function canonicalFeedback(input) {
  const valid = validateFeedbackInput(input);
  return {
    schema: REQUEST_SCHEMA,
    baseline_request_ref: valid.baseline_request_ref,
    result_request_ref: valid.result_request_ref,
    failure_class: valid.failure_class,
    operator: { name: valid.operator.name, version: valid.operator.version },
    outcome: valid.outcome,
    notes: valid.notes,
  };
}

function sortedJson(value) {
  if (Array.isArray(value)) return value.map(sortedJson);
  if (value != null && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, sortedJson(value[key])]));
  }
  return value;
}

function contentMatches(a, b) {
  return JSON.stringify(sortedJson(a)) === JSON.stringify(sortedJson(b));
}

export function makeFeedbackWriter(query, { now = () => new Date().toISOString() } = {}) {
  if (typeof query !== 'function') throw new Error('FEEDBACK_QUERY_REQUIRED');
  return async (input) => {
    const feedback = canonicalFeedback(input);
    const feedbackRef = createHash('sha256')
      .update(RECORD_SCHEMA + '\n', 'utf8')
      .update(JSON.stringify(feedback), 'utf8')
      .digest('hex')
      .slice(0, 32);
    const record = {
      schema: RECORD_SCHEMA,
      feedback_ref: feedbackRef,
      received_at: now(),
      feedback,
    };

    try {
      const inserted = await query(
        'INSERT INTO ' + TABLE + ' (feedback_ref,record) VALUES ($1,$2::jsonb) ' +
          'ON CONFLICT (feedback_ref) DO NOTHING RETURNING feedback_ref',
        [feedbackRef, JSON.stringify(record)],
      );
      const verified = await query(
        'SELECT record FROM ' + TABLE + ' WHERE feedback_ref=$1',
        [feedbackRef],
      );
      if (verified?.length !== 1) throw new Error('FEEDBACK_READBACK_INCOMPLETE');
      if (!contentMatches(verified[0].record?.feedback, feedback)) {
        throw new Error('FEEDBACK_REF_CONFLICT');
      }
      return {
        status: inserted?.length ? 'RECORDED' : 'UNCHANGED',
        feedback_ref: feedbackRef,
      };
    } catch (error) {
      if (error?.message === 'FEEDBACK_REF_CONFLICT' ||
          error?.message === 'FEEDBACK_READBACK_INCOMPLETE') throw error;
      // Never propagate PostgreSQL errors: they may contain private notes.
      throw new Error('FEEDBACK_STORAGE_UNAVAILABLE');
    }
  };
}
