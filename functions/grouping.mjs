const OCCURRENCE_FIELDS = [
  'session_id',
  'message_id',
  'ordinal',
  'session_title',
  'session_coverage',
  'role',
  'search_class',
  'create_time',
  'provider',
  'shard_id',
  'generation_id',
  'shard_rank',
];

function occurrenceFromHit(hit) {
  const occurrence = {};
  for (const field of OCCURRENCE_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(hit, field)) occurrence[field] = hit[field];
  }
  return occurrence;
}

export function groupExactTextHits(hits = []) {
  if (!Array.isArray(hits)) throw new Error('GROUP_HITS_INVALID');

  const groups = [];
  const byText = new Map();
  for (const hit of hits) {
    if (typeof hit?.text !== 'string') throw new Error('GROUP_TEXT_INVALID');

    let group = byText.get(hit.text);
    if (!group) {
      group = {
        text: hit.text,
        representative: Object.fromEntries(
          Object.entries(hit).filter(([key]) => key !== 'text'),
        ),
        occurrence_count: 0,
        occurrences: [],
      };
      byText.set(hit.text, group);
      groups.push(group);
    }

    group.occurrences.push(occurrenceFromHit(hit));
    group.occurrence_count = group.occurrences.length;
  }
  return groups;
}

function validateGroup(group) {
  if (group == null || typeof group !== 'object' || Array.isArray(group)) {
    throw new Error('GROUP_INVALID');
  }
  if (typeof group.text !== 'string') throw new Error('GROUP_TEXT_INVALID');
  if (group.representative == null || typeof group.representative !== 'object'
      || Array.isArray(group.representative)) {
    throw new Error('GROUP_REPRESENTATIVE_INVALID');
  }
  if (!Array.isArray(group.occurrences)) throw new Error('GROUP_OCCURRENCES_INVALID');
  if (group.occurrence_count !== group.occurrences.length) {
    throw new Error('GROUP_OCCURRENCE_COUNT_MISMATCH');
  }
}

export function mergeExactTextGroups(rankedGroups = [], resultLimit = undefined) {
  if (!Array.isArray(rankedGroups)) throw new Error('GROUPS_INVALID');
  const effectiveLimit = resultLimit === undefined ? Math.max(1, rankedGroups.length) : resultLimit;
  if (!Number.isInteger(effectiveLimit) || effectiveLimit < 1) {
    throw new Error('GROUP_RESULT_LIMIT_INVALID');
  }

  const merged = [];
  const byText = new Map();
  for (const inputGroup of rankedGroups) {
    validateGroup(inputGroup);

    let group = byText.get(inputGroup.text);
    if (!group) {
      group = {
        text: inputGroup.text,
        representative: { ...inputGroup.representative },
        occurrence_count: 0,
        occurrences: [],
      };
      byText.set(inputGroup.text, group);
      merged.push(group);
    }

    group.occurrences.push(...inputGroup.occurrences.map((occurrence) => ({ ...occurrence })));
    group.occurrence_count = group.occurrences.length;
  }

  return merged.slice(0, effectiveLimit);
}
