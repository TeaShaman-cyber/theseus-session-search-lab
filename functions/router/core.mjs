import { mergeExactTextGroups } from '../grouping.mjs';

const VALID_SCOPES = new Set(['dialogue', 'evidence', 'trace']);
const VALID_SEARCH_MODES = new Set(['strict', 'recall', 'auto']);
const VALID_FEEDBACK_OUTCOMES = new Set(['improved', 'same', 'regressed', 'mixed', 'unknown']);
const FEEDBACK_REF = /^[0-9a-f]{32}$/;
const FEEDBACK_SLUG = /^[a-z0-9][a-z0-9_.-]{0,127}$/;
const FEEDBACK_FIELDS = new Set([
  'baseline_request_ref',
  'result_request_ref',
  'failure_class',
  'operator',
  'outcome',
  'notes',
]);
const FEEDBACK_OPERATOR_FIELDS = new Set(['name', 'version']);
const FEEDBACK_READ_FIELDS = new Set(['feedback_ref']);
const COVERAGE_FIELDS = new Set(['provider', 'session_id', 'from_time', 'to_time']);
const SOURCE_WATERMARK_STATUS = 'PROVIDER_REPORTED_NO_KNOWN_GAPS';
const SOURCE_WATERMARK_BASIS = 'provider_export_manifest';
const SOURCE_SNAPSHOT_SCOPE = 'GEMINI_APPS_ACTIVITY';
const SOURCE_SNAPSHOT_BASIS = 'provider_takeout_request_receipt';
const SHA256_REF = /^[0-9a-f]{64}$/;

function validateSearchInput(input = {}) {
  if (Object.prototype.hasOwnProperty.call(input, 'grouping')
      || Object.prototype.hasOwnProperty.call(input, 'candidate_budget')
      || Object.prototype.hasOwnProperty.call(input, 'result_shape')) {
    throw new Error('INTERNAL_SEARCH_CONTROL_NOT_PUBLIC');
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
  if (fromTime !== null && toTime !== null && fromTime > toTime) {
    throw new Error('TIME_BOUNDS_INVERTED');
  }

  return {
    ...input,
    query,
    scopes,
    limit,
    search_mode: searchMode,
    session_id: sessionId,
    from_time: fromTime,
    to_time: toTime,
  };
}

export function validateGroupedCanaryInput(input = {}) {
  if (Object.prototype.hasOwnProperty.call(input, 'grouping')) {
    throw new Error('GROUPING_CONTROL_IMPLICIT');
  }
  const { candidate_budget: rawCandidateBudget, ...flatInput } = input;
  const validated = validateSearchInput(flatInput);
  if (validated.search_mode !== 'recall') throw new Error('GROUPED_SEARCH_RECALL_ONLY');

  const candidateBudget = rawCandidateBudget ?? Math.min(300, validated.limit * 3);
  if (!Number.isInteger(candidateBudget)
      || candidateBudget < validated.limit
      || candidateBudget > 300) {
    throw new Error('CANDIDATE_BUDGET_OUT_OF_BOUNDS');
  }
  return { ...validated, candidate_budget: candidateBudget };
}

function assertExactFields(value, allowed, code) {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(code);
  }
  const unknown = Object.keys(value).filter((key) => !allowed.has(key)).sort();
  if (unknown.length) throw new Error(code);
}

function requireFeedbackRef(value, code) {
  if (typeof value !== 'string' || !FEEDBACK_REF.test(value)) throw new Error(code);
  return value;
}

function requireFeedbackSlug(value, code) {
  if (typeof value !== 'string' || !FEEDBACK_SLUG.test(value)) throw new Error(code);
  return value;
}

function validateFeedbackInput(input = {}) {
  assertExactFields(input, FEEDBACK_FIELDS, 'FEEDBACK_FIELDS_INVALID');

  const baselineRequestRef = requireFeedbackRef(
    input.baseline_request_ref,
    'FEEDBACK_BASELINE_REF_INVALID',
  );
  const resultRequestRef = input.result_request_ref == null
    ? null
    : requireFeedbackRef(input.result_request_ref, 'FEEDBACK_RESULT_REF_INVALID');
  const failureClass = requireFeedbackSlug(
    input.failure_class,
    'FEEDBACK_FAILURE_CLASS_INVALID',
  );

  assertExactFields(
    input.operator,
    FEEDBACK_OPERATOR_FIELDS,
    'FEEDBACK_OPERATOR_FIELDS_INVALID',
  );
  const operator = {
    name: requireFeedbackSlug(input.operator.name, 'FEEDBACK_OPERATOR_INVALID'),
    version: requireFeedbackSlug(input.operator.version, 'FEEDBACK_OPERATOR_INVALID'),
  };

  if (!VALID_FEEDBACK_OUTCOMES.has(input.outcome)) {
    throw new Error('FEEDBACK_OUTCOME_INVALID');
  }

  let notes = null;
  if (input.notes != null) {
    if (typeof input.notes !== 'string' || !input.notes.trim() || input.notes.length > 4000) {
      throw new Error('FEEDBACK_NOTES_INVALID');
    }
    notes = input.notes;
  }

  return {
    baseline_request_ref: baselineRequestRef,
    result_request_ref: resultRequestRef,
    failure_class: failureClass,
    operator,
    outcome: input.outcome,
    notes,
  };
}

export function validateCoverageInput(input = {}) {
  assertExactFields(input, COVERAGE_FIELDS, 'COVERAGE_FIELDS_INVALID');
  const provider = input.provider ?? null;
  const sessionId = input.session_id ?? null;
  if (provider !== null && (typeof provider !== 'string' || !provider || provider.length > 64)) {
    throw new Error('COVERAGE_PROVIDER_INVALID');
  }
  if (sessionId !== null && (typeof sessionId !== 'string' || !sessionId || sessionId.length > 512)) {
    throw new Error('COVERAGE_SESSION_INVALID');
  }
  const fromTime = input.from_time ?? null;
  const toTime = input.to_time ?? null;
  if (fromTime === null && toTime === null) throw new Error('COVERAGE_TIME_REQUIRED');
  for (const value of [fromTime, toTime]) {
    if (value !== null && (typeof value !== 'number' || !Number.isFinite(value))) {
      throw new Error('COVERAGE_TIME_INVALID');
    }
  }
  if (fromTime !== null && toTime !== null && fromTime > toTime) {
    throw new Error('COVERAGE_TIME_INVERTED');
  }
  return { provider, session_id: sessionId, from_time: fromTime, to_time: toTime };
}

export function validateFeedbackReadInput(input = {}) {
  assertExactFields(input, FEEDBACK_READ_FIELDS, 'FEEDBACK_READ_FIELDS_INVALID');
  return { feedback_ref: requireFeedbackRef(input.feedback_ref, 'FEEDBACK_REF_INVALID') };
}

export function applySourceWatermarkOverlay(catalog, rawOverlay) {
  if (rawOverlay == null || rawOverlay === '') return catalog;

  let overlay;
  try {
    overlay = JSON.parse(rawOverlay);
  } catch {
    throw new Error('SESSION_SEARCH_SOURCE_WATERMARKS_INVALID');
  }
  if (overlay == null || typeof overlay !== 'object' || Array.isArray(overlay)) {
    throw new Error('SESSION_SEARCH_SOURCE_WATERMARKS_INVALID');
  }

  const providers = Object.keys(overlay);
  if (providers.length === 0) throw new Error('SESSION_SEARCH_SOURCE_WATERMARKS_INVALID');
  const catalogProviders = new Set(catalog.map((item) => item.provider));
  const allowedFields = new Set([
    'observed_through',
    'status',
    'basis',
    'accepted_set_sha256',
  ]);

  for (const provider of providers) {
    if (typeof provider !== 'string' || !provider || provider.length > 64
        || !catalogProviders.has(provider)) {
      throw new Error('SESSION_SEARCH_SOURCE_WATERMARKS_INVALID');
    }
    const value = overlay[provider];
    if (value == null || typeof value !== 'object' || Array.isArray(value)
        || Object.keys(value).some((key) => !allowedFields.has(key))
        || Object.keys(value).length !== allowedFields.size
        || normalizedSourceWatermark({ source_watermark: value }) == null) {
      throw new Error('SESSION_SEARCH_SOURCE_WATERMARKS_INVALID');
    }
  }

  return catalog.map((item) => (
    Object.hasOwn(overlay, item.provider)
      ? { ...item, source_watermark: { ...overlay[item.provider] } }
      : item
  ));
}

function normalizedSourceSnapshot(shard) {
  const value = shard?.source_snapshot;
  if (value == null || typeof value !== 'object' || Array.isArray(value)) return null;
  const fields = Object.keys(value).sort();
  const expected = ['accepted_set_sha256', 'basis', 'observed_through', 'source_scope'];
  if (fields.length !== expected.length || fields.some((field, index) => field !== expected[index])) return null;
  if (value.source_scope !== SOURCE_SNAPSHOT_SCOPE || value.basis !== SOURCE_SNAPSHOT_BASIS) {
    return null;
  }
  if (!Number.isFinite(value.observed_through)) return null;
  if (typeof value.accepted_set_sha256 !== 'string'
      || !SHA256_REF.test(value.accepted_set_sha256)) {
    return null;
  }
  return {
    source_scope: value.source_scope,
    observed_through: value.observed_through,
    basis: value.basis,
    accepted_set_sha256: value.accepted_set_sha256,
  };
}

export function applySourceSnapshotOverlay(catalog, rawOverlay) {
  if (rawOverlay == null || rawOverlay === '') return catalog;

  let overlay;
  try {
    overlay = JSON.parse(rawOverlay);
  } catch {
    throw new Error('SESSION_SEARCH_SOURCE_SNAPSHOTS_INVALID');
  }
  if (overlay == null || typeof overlay !== 'object' || Array.isArray(overlay)) {
    throw new Error('SESSION_SEARCH_SOURCE_SNAPSHOTS_INVALID');
  }

  const providers = Object.keys(overlay);
  if (providers.length === 0) throw new Error('SESSION_SEARCH_SOURCE_SNAPSHOTS_INVALID');
  const catalogProviders = new Set(catalog.map((item) => item.provider));

  for (const provider of providers) {
    if (provider !== 'gemini' || !catalogProviders.has(provider)) {
      throw new Error('SESSION_SEARCH_SOURCE_SNAPSHOTS_INVALID');
    }
    if (normalizedSourceSnapshot({ source_snapshot: overlay[provider] }) == null) {
      throw new Error('SESSION_SEARCH_SOURCE_SNAPSHOTS_INVALID');
    }
  }

  return catalog.map((item) => (
    Object.hasOwn(overlay, item.provider)
      ? { ...item, source_snapshot: { ...overlay[item.provider] } }
      : item
  ));
}

export function selectShards(catalog, input = {}) {
  return [...catalog]
    .filter((shard) => input.provider == null || shard.provider === input.provider)
    .filter((shard) => {
      if (input.session_id == null || shard.session_ids == null) return true;
      return shard.session_ids.includes(input.session_id);
    })
    .filter((shard) => {
      if (input.from_time != null && shard.time_max != null && shard.time_max < input.from_time) return false;
      if (input.to_time != null && shard.time_min != null && shard.time_min > input.to_time) return false;
      return true;
    })
    .sort((a, b) => a.shard_id.localeCompare(b.shard_id));
}

function normalizedSourceWatermark(shard) {
  const value = shard?.source_watermark;
  if (value == null || typeof value !== 'object' || Array.isArray(value)) return null;
  if (!Number.isFinite(value.observed_through)) return null;
  if (value.status !== SOURCE_WATERMARK_STATUS || value.basis !== SOURCE_WATERMARK_BASIS) {
    return null;
  }
  if (typeof value.accepted_set_sha256 !== 'string'
      || !SHA256_REF.test(value.accepted_set_sha256)) {
    return null;
  }
  return {
    observed_through: value.observed_through,
    status: value.status,
    basis: value.basis,
    accepted_set_sha256: value.accepted_set_sha256,
  };
}

function summarizeSourceCurrentness(eligible, input) {
  const unknown = { source_currentness: 'UNKNOWN_WITHOUT_SOURCE_WATERMARK' };
  if (input.provider == null || eligible.length === 0) return unknown;

  const watermarks = eligible.map(normalizedSourceWatermark);
  const snapshots = eligible.map(normalizedSourceSnapshot);
  const allWatermarks = watermarks.every((watermark) => watermark != null);
  const allSnapshots = snapshots.every((snapshot) => snapshot != null);

  if (allWatermarks && allSnapshots) return unknown;

  if (allSnapshots) {
    const first = snapshots[0];
    const sameBinding = snapshots.every((snapshot) =>
      snapshot.source_scope === first.source_scope
      && snapshot.observed_through === first.observed_through
      && snapshot.basis === first.basis
      && snapshot.accepted_set_sha256 === first.accepted_set_sha256);
    if (!sameBinding) return unknown;

    const requestExtendsPastSnapshot = input.to_time == null
      || input.to_time > first.observed_through;
    return {
      source_currentness: requestExtendsPastSnapshot
        ? 'STALE_AFTER_BOUNDED_PROVIDER_EXPORT_SNAPSHOT'
        : 'VERIFIED_TO_BOUNDED_PROVIDER_EXPORT_SNAPSHOT',
      source_snapshot: {
        source_scope: first.source_scope,
        observed_through: first.observed_through,
        basis: first.basis,
      },
    };
  }

  if (!allWatermarks) return unknown;

  const first = watermarks[0];
  const sameBinding = watermarks.every((watermark) =>
    watermark.observed_through === first.observed_through
    && watermark.status === first.status
    && watermark.basis === first.basis
    && watermark.accepted_set_sha256 === first.accepted_set_sha256);
  if (!sameBinding) return unknown;

  const requestExtendsPastWatermark = input.to_time == null
    || input.to_time > first.observed_through;
  return {
    source_currentness: requestExtendsPastWatermark
      ? 'STALE_AFTER_PROVIDER_REPORTED_WATERMARK'
      : 'VERIFIED_TO_PROVIDER_REPORTED_WATERMARK',
    source_watermark: {
      observed_through: first.observed_through,
      status: first.status,
      basis: first.basis,
    },
  };
}

// Observed catalog time bounds are not an upstream source freshness guarantee.
// A source watermark is accepted only when every provider-scoped eligible shard
// carries the same accepted-set binding and provider-report metadata.
export function summarizeTimeWindowCoverage(catalog, input, selected) {
  if (input.from_time == null && input.to_time == null) return null;

  const eligible = selectShards(catalog, {
    ...input,
    from_time: null,
    to_time: null,
  });
  const catalogBoundsComplete = eligible.length > 0 && eligible.every((shard) =>
    Number.isFinite(shard.time_min) && Number.isFinite(shard.time_max)
    && shard.time_min <= shard.time_max);
  const knownMaxTimes = eligible
    .map((shard) => shard.time_max)
    .filter((time) => Number.isFinite(time));
  const observedMax = knownMaxTimes.length ? Math.max(...knownMaxTimes) : null;
  const sourceCurrentness = summarizeSourceCurrentness(eligible, input);

  return {
    window_fit: eligible.length === 0 || !catalogBoundsComplete
      ? 'UNKNOWN_COVERAGE'
      : selected.length === 0
        ? 'OUTSIDE_OBSERVED_INDEX_RANGE'
        : 'OBSERVED_POSSIBLE',
    ...sourceCurrentness,
    eligible_shard_count: eligible.length,
    selected_shard_count: selected.length,
    catalog_observed_max_time: observedMax,
    catalog_bounds_complete: catalogBoundsComplete,
  };
}

export function mergeShardOutcomes(selected, outcomes, limit) {
  const queried = [];
  const unavailable = [];
  const ranked = [];

  for (const shard of selected) {
    if (shard.state && shard.state !== 'READY') {
      unavailable.push({ shard_id: shard.shard_id, reason: 'NOT_READY' });
      continue;
    }
    queried.push(shard.shard_id);
    const outcome = outcomes.find((item) => item.shard.shard_id === shard.shard_id);
    if (!outcome || !outcome.ok) {
      unavailable.push({ shard_id: shard.shard_id, reason: outcome?.reason ?? 'QUERY_FAILED' });
      continue;
    }
    if (outcome.body?.generation_id !== shard.generation_id) {
      unavailable.push({ shard_id: shard.shard_id, reason: 'STALE_GENERATION' });
      continue;
    }
    for (let i = 0; i < (outcome.body.hits ?? []).length; i += 1) {
      const original = outcome.body.hits[i];
      const rank = i + 1;
      ranked.push({
        reciprocal: 1 / (60 + rank),
        shard_id: shard.shard_id,
        hit: {
          ...original,
          shard_id: shard.shard_id,
          provider: shard.provider,
          generation_id: shard.generation_id,
          shard_rank: rank,
        },
      });
    }
  }

  ranked.sort((a, b) =>
    b.reciprocal - a.reciprocal ||
    a.shard_id.localeCompare(b.shard_id) ||
    String(a.hit.session_id ?? '').localeCompare(String(b.hit.session_id ?? '')) ||
    Number(a.hit.ordinal ?? 0) - Number(b.hit.ordinal ?? 0) ||
    String(a.hit.message_id ?? '').localeCompare(String(b.hit.message_id ?? ''))
  );
  unavailable.sort((a, b) => a.shard_id.localeCompare(b.shard_id));
  const candidateCount = ranked.length;
  const hits = ranked.slice(0, limit).map((item) => item.hit);
  return {
    status: unavailable.length ? 'PARTIAL' : hits.length ? 'FOUND' : 'NO_SIGNAL',
    hits,
    candidate_count: candidateCount,
    queried_shards: queried,
    unavailable_shards: unavailable,
  };
}

function normalizeShardGroup(shard, group) {
  if (group == null || typeof group !== 'object' || Array.isArray(group)) {
    throw new Error('GROUP_PAYLOAD_INVALID');
  }
  if (typeof group.text !== 'string') throw new Error('GROUP_PAYLOAD_INVALID');
  if (group.representative == null || typeof group.representative !== 'object'
      || Array.isArray(group.representative)) {
    throw new Error('GROUP_PAYLOAD_INVALID');
  }
  if (!Array.isArray(group.occurrences) || group.occurrence_count !== group.occurrences.length) {
    throw new Error('GROUP_PAYLOAD_INVALID');
  }
  return {
    ...group,
    representative: {
      ...group.representative,
      provider: shard.provider,
      shard_id: shard.shard_id,
      generation_id: shard.generation_id,
    },
    occurrences: group.occurrences.map((occurrence) => ({
      ...occurrence,
      provider: shard.provider,
      shard_id: shard.shard_id,
      generation_id: shard.generation_id,
    })),
  };
}

export function mergeGroupedShardOutcomes(selected, outcomes, limit) {
  const queried = [];
  const unavailable = [];
  const ranked = [];

  for (const shard of selected) {
    if (shard.state && shard.state !== 'READY') {
      unavailable.push({ shard_id: shard.shard_id, reason: 'NOT_READY' });
      continue;
    }
    queried.push(shard.shard_id);
    const outcome = outcomes.find((item) => item.shard.shard_id === shard.shard_id);
    if (!outcome || !outcome.ok) {
      unavailable.push({ shard_id: shard.shard_id, reason: outcome?.reason ?? 'QUERY_FAILED' });
      continue;
    }
    if (outcome.body?.generation_id !== shard.generation_id) {
      unavailable.push({ shard_id: shard.shard_id, reason: 'STALE_GENERATION' });
      continue;
    }
    if (outcome.body?.grouping !== 'exact_text_v1' || !Array.isArray(outcome.body?.groups)) {
      unavailable.push({ shard_id: shard.shard_id, reason: 'GROUPING_NOT_CONFIRMED' });
      continue;
    }
    let groups;
    try {
      groups = outcome.body.groups.map((group) => normalizeShardGroup(shard, group));
    } catch {
      unavailable.push({ shard_id: shard.shard_id, reason: 'GROUP_PAYLOAD_INVALID' });
      continue;
    }
    for (let i = 0; i < groups.length; i += 1) {
      const rank = i + 1;
      ranked.push({
        reciprocal: 1 / (60 + rank),
        shard_id: shard.shard_id,
        group: groups[i],
      });
    }
  }

  ranked.sort((a, b) =>
    b.reciprocal - a.reciprocal
    || a.shard_id.localeCompare(b.shard_id)
    || String(a.group?.text ?? '').localeCompare(String(b.group?.text ?? ''))
    || String(a.group?.representative?.session_id ?? '').localeCompare(String(b.group?.representative?.session_id ?? ''))
    || Number(a.group?.representative?.ordinal ?? 0) - Number(b.group?.representative?.ordinal ?? 0)
    || String(a.group?.representative?.message_id ?? '').localeCompare(String(b.group?.representative?.message_id ?? ''))
  );
  unavailable.sort((a, b) => a.shard_id.localeCompare(b.shard_id));

  const candidateCount = outcomes.reduce((count, outcome) =>
    count + (outcome?.ok && Number.isInteger(outcome.body?.candidate_count)
      ? outcome.body.candidate_count
      : 0), 0);
  const candidateGroupCount = ranked.length;
  const groups = mergeExactTextGroups(ranked.map((item) => item.group), limit);
  return {
    status: unavailable.length ? 'PARTIAL' : groups.length ? 'FOUND' : 'NO_SIGNAL',
    grouping: 'exact_text_v1',
    groups,
    candidate_count: candidateCount,
    candidate_group_count: candidateGroupCount,
    returned_group_count: groups.length,
    queried_shards: queried,
    unavailable_shards: unavailable,
  };
}

export async function handleMcpRequest(request, searchFn, feedbackFn = null, browseFn = null, feedbackReadFn = null, coverageFn = null) {
  const id = request?.id ?? null;
  const method = request?.method;

  if (method === 'initialize') {
    return {
      jsonrpc: '2.0',
      id,
      result: {
        protocolVersion: request?.params?.protocolVersion ?? '2025-06-18',
        capabilities: { tools: {} },
        serverInfo: { name: 'theseus-session-search', version: '0.1.0' },
      },
    };
  }
  if (method === 'ping') return { jsonrpc: '2.0', id, result: {} };
  if (method === 'notifications/initialized') return null;
  if (method === 'tools/list') {
    const tools = [{
      name: 'session_search',
      description: 'Search federated Session Search shards. Exact duplicate text is returned once with every historical occurrence/provenance attached; PARTIAL remains explicit. If results are insufficient and a language mismatch is plausible, optionally try a separate translated-query search; keep original results and ranks. After comparing outcomes, optionally record honest session_feedback when available (use unknown if unresolved).',
      inputSchema: {
        type: 'object',
        additionalProperties: false,
        required: ['query'],
        properties: {
          query: { type: 'string', minLength: 1, maxLength: 512 },
          scopes: { type: 'array', items: { enum: ['dialogue', 'evidence', 'trace'] }, minItems: 1 },
          limit: { type: 'integer', minimum: 1, maximum: 100, default: 8 },
          search_mode: { type: 'string', enum: ['strict', 'recall', 'auto'], default: 'strict' },
          provider: { type: 'string' },
          session_id: { type: 'string' },
          from_time: { type: 'number' },
          to_time: { type: 'number' },
        },
      },
    }];
    if (typeof coverageFn === 'function') {
      tools.push({
        name: 'session_coverage',
        description: 'Preflight observed indexed time range BEFORE searching for recent conversations. For a requested provider and from_time/to_time, report whether the observed shard catalog intersects the window; never treat indexed bounds as proof that original conversation history is complete or current. No transcript or model query is performed.',
        inputSchema: {
          type: 'object', additionalProperties: false, required: [],
          anyOf: [{ required: ['from_time'] }, { required: ['to_time'] }],
          properties: {
            provider: { type: 'string', minLength: 1, maxLength: 64 },
            session_id: { type: 'string', minLength: 1, maxLength: 512 },
            from_time: { type: 'number' },
            to_time: { type: 'number' },
          },
        },
      });
    }
    if (typeof browseFn === 'function') {
      tools.push({
        name: 'session_browse',
        description: 'Read a selected session/branch in bounded ordered pages, pinned to the exact shard and generation. Does not imply the original exported history is complete.',
        inputSchema: {
          type: 'object', additionalProperties: false,
          required: ['session_id', 'shard_id', 'generation_id'],
          properties: {
            session_id: { type: 'string', minLength: 1, maxLength: 512 },
            shard_id: { type: 'string', minLength: 1 },
            generation_id: { type: 'string', pattern: '^[0-9a-f]{64}$' },
            limit: { type: 'integer', minimum: 1, maximum: 100, default: 20 },
            max_chars: { type: 'integer', minimum: 1, maximum: 100000, default: 16000 },
            start_ordinal: { type: 'integer', minimum: 0 },
            cursor: { type: 'string' },
          },
        },
      });
    }
    if (typeof feedbackFn === 'function') {
      tools.push({
        name: 'session_feedback',
        description: 'Record explicit retrieval feedback for a prior Session Search request without changing serving policy.',
        inputSchema: {
          type: 'object',
          additionalProperties: false,
          required: ['baseline_request_ref', 'failure_class', 'operator', 'outcome'],
          properties: {
            baseline_request_ref: { type: 'string', pattern: '^[0-9a-f]{32}$' },
            result_request_ref: { type: 'string', pattern: '^[0-9a-f]{32}$' },
            failure_class: { type: 'string', pattern: '^[a-z0-9][a-z0-9_.-]{0,127}$' },
            operator: {
              type: 'object',
              additionalProperties: false,
              required: ['name', 'version'],
              properties: {
                name: { type: 'string', pattern: '^[a-z0-9][a-z0-9_.-]{0,127}$' },
                version: { type: 'string', pattern: '^[a-z0-9][a-z0-9_.-]{0,127}$' },
              },
            },
            outcome: {
              type: 'string',
              enum: ['improved', 'same', 'regressed', 'mixed', 'unknown'],
            },
            notes: { type: 'string', minLength: 1, maxLength: 4000 },
          },
        },
      });
    }
    if (typeof feedbackReadFn === 'function') {
      tools.push({
        name: 'session_feedback_get',
        description: 'Read verified feedback by exact feedback_ref in the existing private authenticated Session Search App, including bounded notes (max 4000 characters). No listing or search policy changes.',
        inputSchema: {
          type: 'object', additionalProperties: false,
          required: ['feedback_ref'],
          properties: { feedback_ref: { type: 'string', pattern: '^[0-9a-f]{32}$' } },
        },
      });
    }
    return {
      jsonrpc: '2.0',
      id,
      result: { tools },
    };
  }
  if (method === 'tools/call') {
    const toolName = request?.params?.name;
    if (toolName === 'session_coverage' && typeof coverageFn === 'function') {
      try {
        const input = validateCoverageInput(request.params.arguments ?? {});
        const result = await coverageFn(input);
        return { jsonrpc: '2.0', id, result: {
          content: [{ type: 'text', text: JSON.stringify(result) }],
          structuredContent: result,
          isError: false,
        } };
      } catch (error) {
        return { jsonrpc: '2.0', id, result: {
          content: [{ type: 'text', text: String(error?.message ?? error) }],
          isError: true,
        } };
      }
    }
    if (toolName === 'session_browse' && typeof browseFn === 'function') {
      try {
        const result = await browseFn(request.params.arguments ?? {});
        return {
          jsonrpc: '2.0', id,
          result: {
            content: [{ type: 'text', text: JSON.stringify(result) }],
            structuredContent: result,
            isError: false,
          },
        };
      } catch (error) {
        return { jsonrpc: '2.0', id, result: {
          content: [{ type: 'text', text: String(error?.message ?? error) }],
          isError: true,
        } };
      }
    }
    if (toolName === 'session_feedback') {
      if (typeof feedbackFn !== 'function') {
        return { jsonrpc: '2.0', id, error: { code: -32602, message: 'unknown tool' } };
      }
      try {
        const input = validateFeedbackInput(request.params.arguments ?? {});
        const result = await feedbackFn(input);
        return {
          jsonrpc: '2.0',
          id,
          result: {
            content: [{ type: 'text', text: JSON.stringify(result) }],
            structuredContent: result,
            isError: false,
          },
        };
      } catch (error) {
        return {
          jsonrpc: '2.0',
          id,
          result: {
            content: [{ type: 'text', text: String(error?.message ?? error) }],
            isError: true,
          },
        };
      }
    }
    if (toolName === 'session_feedback_get' && typeof feedbackReadFn === 'function') {
      try {
        const input = validateFeedbackReadInput(request.params.arguments ?? {});
        const result = await feedbackReadFn(input);
        return { jsonrpc: '2.0', id, result: {
          content: [{ type: 'text', text: JSON.stringify(result) }],
          structuredContent: result, isError: false,
        } };
      } catch (error) {
        return { jsonrpc: '2.0', id, result: {
          content: [{ type: 'text', text: String(error?.message ?? error) }],
          isError: true,
        } };
      }
    }
    if (toolName !== 'session_search') {
      return { jsonrpc: '2.0', id, error: { code: -32602, message: 'unknown tool' } };
    }
    try {
      const input = validateSearchInput(request.params.arguments ?? {});
      const result = await searchFn(input);
      return {
        jsonrpc: '2.0',
        id,
        result: {
          content: [{ type: 'text', text: JSON.stringify(result) }],
          structuredContent: result,
          isError: false,
        },
      };
    } catch (error) {
      return {
        jsonrpc: '2.0',
        id,
        result: {
          content: [{ type: 'text', text: String(error?.message ?? error) }],
          isError: true,
        },
      };
    }
  }
  return { jsonrpc: '2.0', id, error: { code: -32601, message: 'method not found' } };
}

export { validateFeedbackInput, validateSearchInput };
