import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';

import { projectCandidateV1, projectServerlessCandidateV1 } from '../candidate-contract.mjs';

const fixtureUrl = new URL('../../tests/fixtures/candidate_v1.json', import.meta.url);

test('JavaScript matches shared local and serverless candidate v1 fixture', async () => {
  const fixture = JSON.parse(await fs.readFile(fixtureUrl, 'utf8'));
  for (const item of Object.values(fixture)) {
    assert.deepEqual(projectCandidateV1(item.input, { scoreOrder: item.score_order }), item.expected);
  }
});

test('serverless adapter binds Postgres score orientation', async () => {
  const fixture = JSON.parse(await fs.readFile(fixtureUrl, 'utf8'));
  assert.deepEqual(projectServerlessCandidateV1(fixture.serverless.input), fixture.serverless.expected);
});

test('candidate v1 rejects ambiguous score orientation', () => {
  assert.throws(
    () => projectCandidateV1({ session_id: 's', ordinal: 0, text: 'x', search_class: 'dialogue', score: 1 }, { scoreOrder: 'unknown' }),
    /CANDIDATE_SCORE_ORDER_INVALID/,
  );
});
