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
