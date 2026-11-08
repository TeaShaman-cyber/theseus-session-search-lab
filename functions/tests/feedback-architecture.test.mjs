import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

test('production router does not select a feedback storage vendor', async () => {
  const source = await readFile(new URL('../router/index.mjs', import.meta.url), 'utf8');

  assert.equal(source.includes('feedback/upstash'), false);
  assert.equal(source.includes('SESSION_SEARCH_FEEDBACK_UPSTASH'), false);
});
