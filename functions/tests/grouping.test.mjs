import test from 'node:test';
import assert from 'node:assert/strict';

import { groupExactTextHits } from '../grouping.mjs';

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
  assert.deepEqual(groupExactTextHits(fixture.hits), fixture.expected);
});
