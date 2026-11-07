export const CANDIDATE_SCHEMA_V1 = 'theseus.session-search-candidate.v1';

const VALID_SEARCH_CLASSES = new Set(['dialogue', 'evidence', 'trace']);
const VALID_SCORE_ORDERS = new Set(['lower_better', 'higher_better']);
const OPTIONAL_FIELDS = [
  'session_title', 'session_coverage', 'message_id', 'role', 'content_type',
  'create_time', 'provider', 'shard_id', 'generation_id', 'shard_rank',
];

function finiteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

export function projectCandidateV1(row, { scoreOrder } = {}) {
  if (!VALID_SCORE_ORDERS.has(scoreOrder)) throw new Error('CANDIDATE_SCORE_ORDER_INVALID');
  if (row == null || typeof row !== 'object' || Array.isArray(row)) throw new Error('CANDIDATE_ROW_INVALID');
  if (typeof row.session_id !== 'string' || row.session_id.length === 0) throw new Error('CANDIDATE_SESSION_INVALID');
  if (!Number.isInteger(row.ordinal) || row.ordinal < 0) throw new Error('CANDIDATE_ORDINAL_INVALID');
  if (typeof row.text !== 'string') throw new Error('CANDIDATE_TEXT_INVALID');
  if (!VALID_SEARCH_CLASSES.has(row.search_class)) throw new Error('CANDIDATE_SEARCH_CLASS_INVALID');
  if (!finiteNumber(row.score)) throw new Error('CANDIDATE_SCORE_INVALID');

  const result = {
    schema: CANDIDATE_SCHEMA_V1,
    session_id: row.session_id,
    ordinal: row.ordinal,
    search_class: row.search_class,
    text: row.text,
  };
  for (const field of OPTIONAL_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(row, field)) result[field] = row[field];
  }

  if (result.message_id != null && typeof result.message_id !== 'string') throw new Error('CANDIDATE_MESSAGE_ID_INVALID');
  if (result.create_time != null && !finiteNumber(result.create_time)) throw new Error('CANDIDATE_CREATE_TIME_INVALID');
  if (result.shard_rank != null && (!Number.isInteger(result.shard_rank) || result.shard_rank < 1)) {
    throw new Error('CANDIDATE_SHARD_RANK_INVALID');
  }

  result.backend_score = row.score;
  result.backend_score_order = scoreOrder;
  return result;
}
