import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, '..', 'router', 'index.mjs'), 'utf8');

test('production router passes configured semanticEvents into makeRouterHandler', () => {
  assert.match(source, /makeRouterHandler\(\{[^}]*semanticEvents[^}]*\}\)/s);
});
