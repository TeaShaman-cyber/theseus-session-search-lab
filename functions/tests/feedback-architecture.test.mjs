import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

test('production router does not select a feedback storage vendor', async () => {
  const source = await readFile(new URL('../router/index.mjs', import.meta.url), 'utf8');

  assert.equal(source.includes('feedback/upstash'), false);
  assert.equal(source.includes('SESSION_SEARCH_FEEDBACK_UPSTASH'), false);
});
test('production router leaves grouped canary disabled', async () => {
  const source = await readFile(new URL('../router/index.mjs', import.meta.url), 'utf8');

  assert.equal(source.includes('groupedCanaryEnabled'), false);
  assert.equal(source.includes('SESSION_SEARCH_GROUPED_CANARY'), false);
});
test('production router defaults to grouped results with an explicit internal rollback setting', async () => {
  const source = await readFile(new URL('../router/index.mjs', import.meta.url), 'utf8');

  assert.match(source, /SESSION_SEARCH_RESULT_SHAPE \?\? 'grouped_v1'/);
  assert.match(source, /resultShape/);
});
