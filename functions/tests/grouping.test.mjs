import test from 'node:test';
import assert from 'node:assert/strict';

import { groupExactTextHits, mergeExactTextGroups } from '../grouping.mjs';

test('exact text group keeps best-ranked representative and every candidate occurrence', () => {
  const hits = [
    { session_id: 'a', message_id: 'm1', ordinal: 1, text: 'same', score: 9, provider: 'chatgpt', shard_id: 's1', generation_id: 'g1', shard_rank: 1, search_class: 'dialogue' },
    { session_id: 'b', message_id: 'm2', ordinal: 2, text: 'same', score: 8, provider: 'deepseek', shard_id: 's2', generation_id: 'g2', shard_rank: 1, search_class: 'dialogue' },
    { session_id: 'c', message_id: 'm1', ordinal: 1, text: 'other', score: 7, provider: 'deepseek', shard_id: 's2', generation_id: 'g2', shard_rank: 1, search_class: 'dialogue' },
  ];

  const groups = groupExactTextHits(hits);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].text, 'same');
  assert.equal(groups[0].occurrence_count, 2);
  const { text: _text, ...expectedRepresentative } = hits[0];
  assert.deepEqual(groups[0].representative, expectedRepresentative);
  assert.equal(Object.hasOwn(groups[0].representative, 'text'), false);
  assert.deepEqual(groups[0].occurrences, [
    { session_id: 'a', message_id: 'm1', ordinal: 1, search_class: 'dialogue', provider: 'chatgpt', shard_id: 's1', generation_id: 'g1', shard_rank: 1 },
    { session_id: 'b', message_id: 'm2', ordinal: 2, search_class: 'dialogue', provider: 'deepseek', shard_id: 's2', generation_id: 'g2', shard_rank: 1 },
  ]);
  assert.equal(groups[1].text, 'other');
  assert.equal(groups[1].occurrence_count, 1);
});

test('same message id with different exact text never collapses', () => {
  const groups = groupExactTextHits([
    { session_id: 'a', message_id: 'shared', ordinal: 0, text: 'alpha' },
    { session_id: 'b', message_id: 'shared', ordinal: 0, text: 'beta' },
  ]);
  assert.equal(groups.length, 2);
});

test('group order follows first ranked occurrence and occurrence payload does not repeat text', () => {
  const hits = [
    { session_id: 'a', message_id: '1', ordinal: 0, text: 'zeta' },
    { session_id: 'b', message_id: '2', ordinal: 0, text: 'alpha' },
    { session_id: 'c', message_id: '3', ordinal: 0, text: 'zeta' },
  ];
  const groups = groupExactTextHits(hits);
  assert.deepEqual(groups.map((g) => g.text), ['zeta', 'alpha']);
  assert.equal(Object.hasOwn(groups[0].occurrences[0], 'text'), false);
  assert.equal(groups.reduce((n, g) => n + g.occurrences.length, 0), hits.length);
});

test('invalid hit text fails closed instead of inventing an identity', () => {
  assert.throws(() => groupExactTextHits([{ session_id: 'a', text: null }]), /GROUP_TEXT_INVALID/);
});

test('JavaScript matches shared exact-text grouping v1 conformance fixture', async () => {
  const fs = await import('node:fs/promises');
  const url = new URL('../../tests/fixtures/exact_text_grouping_v1.json', import.meta.url);
  const fixture = JSON.parse(await fs.readFile(url, 'utf8'));
  const groups = groupExactTextHits(fixture.hits);
  assert.deepEqual(groups, fixture.expected);
  assert.equal(
    groups.reduce((count, group) => count + group.occurrence_count, 0),
    fixture.hits.length,
  );
});


test('router-level exact group merge keeps empty composition safe while rejecting explicit zero limit', () => {
  assert.deepEqual(mergeExactTextGroups([]), []);
  assert.throws(() => mergeExactTextGroups([], 0), /GROUP_RESULT_LIMIT_INVALID/);
});

test('router-level exact group merge preserves cross-shard occurrences and first representative', () => {
  const rankedGroups = [
    {
      text: 'same',
      representative: { session_id: 'a', provider: 'chatgpt', shard_id: 's1', shard_rank: 1 },
      occurrence_count: 2,
      occurrences: [
        { session_id: 'a', message_id: 'm1', provider: 'chatgpt', shard_id: 's1' },
        { session_id: 'b', message_id: 'm2', provider: 'chatgpt', shard_id: 's1' },
      ],
    },
    {
      text: 'other',
      representative: { session_id: 'c', provider: 'chatgpt', shard_id: 's1', shard_rank: 2 },
      occurrence_count: 1,
      occurrences: [
        { session_id: 'c', message_id: 'shared', provider: 'chatgpt', shard_id: 's1' },
      ],
    },
    {
      text: 'same',
      representative: { session_id: 'd', provider: 'deepseek', shard_id: 's2', shard_rank: 1 },
      occurrence_count: 1,
      occurrences: [
        { session_id: 'd', message_id: 'm3', provider: 'deepseek', shard_id: 's2' },
      ],
    },
    {
      text: 'different body',
      representative: { session_id: 'e', provider: 'deepseek', shard_id: 's2', shard_rank: 2 },
      occurrence_count: 1,
      occurrences: [
        { session_id: 'e', message_id: 'shared', provider: 'deepseek', shard_id: 's2' },
      ],
    },
  ];

  const merged = mergeExactTextGroups(rankedGroups, 10);
  assert.deepEqual(merged.map((group) => group.text), ['same', 'other', 'different body']);
  assert.deepEqual(merged[0].representative, rankedGroups[0].representative);
  assert.equal(merged[0].occurrence_count, 3);
  assert.deepEqual(
    merged[0].occurrences.map((occurrence) => [occurrence.provider, occurrence.shard_id]),
    [['chatgpt', 's1'], ['chatgpt', 's1'], ['deepseek', 's2']],
  );
  assert.equal(
    merged.reduce((count, group) => count + group.occurrence_count, 0),
    rankedGroups.reduce((count, group) => count + group.occurrence_count, 0),
  );
  assert.equal(merged[1].occurrences[0].message_id, 'shared');
  assert.equal(merged[2].occurrences[0].message_id, 'shared');
});

test('router-level exact group merge applies result limit after merging selected-group occurrences', () => {
  const groups = mergeExactTextGroups([
    { text: 'same', representative: { shard_id: 's1' }, occurrence_count: 1, occurrences: [{ shard_id: 's1' }] },
    { text: 'other', representative: { shard_id: 's1' }, occurrence_count: 1, occurrences: [{ shard_id: 's1' }] },
    { text: 'same', representative: { shard_id: 's2' }, occurrence_count: 1, occurrences: [{ shard_id: 's2' }] },
  ], 1);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].text, 'same');
  assert.equal(groups[0].occurrence_count, 2);
});

test('router-level exact group merge fails closed on inconsistent occurrence count', () => {
  assert.throws(
    () => mergeExactTextGroups([
      { text: 'same', representative: {}, occurrence_count: 2, occurrences: [{}] },
    ], 1),
    /GROUP_OCCURRENCE_COUNT_MISMATCH/,
  );
});
