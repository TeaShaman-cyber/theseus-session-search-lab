import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const text = await readFile(new URL('./dist/shard/index.mjs', import.meta.url), 'utf8');
for (const builtin of ['events', 'stream', 'util']) {
  assert.equal(
    text.includes(`__require("${builtin}")`),
    false,
    `shard bundle contains unsupported dynamic require of ${builtin}`,
  );
}
console.log('BUNDLE_STATIC_REQUIRE_PASS');
