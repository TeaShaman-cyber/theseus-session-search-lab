import test from 'node:test';
import assert from 'node:assert/strict';

import { applySourceSnapshotOverlay, applySourceWatermarkOverlay } from '../router/core.mjs';

const catalog = [
  { shard_id: 'chatgpt-0001', provider: 'chatgpt', generation_id: 'a'.repeat(64), url: 'https://a.test/search' },
  { shard_id: 'chatgpt-0002', provider: 'chatgpt', generation_id: 'b'.repeat(64), url: 'https://b.test/search' },
  { shard_id: 'deepseek-0001', provider: 'deepseek', generation_id: 'c'.repeat(64), url: 'https://c.test/search' },
];

const watermark = {
  observed_through: 450,
  status: 'PROVIDER_REPORTED_NO_KNOWN_GAPS',
  basis: 'provider_export_manifest',
  accepted_set_sha256: 'd'.repeat(64),
};

test('source watermark overlay is additive and leaves unrelated providers untouched', () => {
  const applied = applySourceWatermarkOverlay(
    catalog,
    JSON.stringify({ chatgpt: watermark }),
  );
  assert.equal(applied.length, 3);
  assert.deepEqual(applied[0].source_watermark, watermark);
  assert.deepEqual(applied[1].source_watermark, watermark);
  assert.equal(Object.hasOwn(applied[2], 'source_watermark'), false);
  assert.equal(Object.hasOwn(catalog[0], 'source_watermark'), false);
});

test('missing source watermark overlay preserves the exact catalog object', () => {
  assert.equal(applySourceWatermarkOverlay(catalog, null), catalog);
  assert.equal(applySourceWatermarkOverlay(catalog, ''), catalog);
});

test('source watermark overlay fails closed on malformed or ambiguous config', () => {
  const bad = [
    '{',
    '[]',
    JSON.stringify({ unknown: watermark }),
    JSON.stringify({ chatgpt: { ...watermark, extra: true } }),
    JSON.stringify({ chatgpt: { ...watermark, accepted_set_sha256: 'nope' } }),
    JSON.stringify({ chatgpt: { ...watermark, observed_through: null } }),
  ];
  for (const raw of bad) {
    assert.throws(
      () => applySourceWatermarkOverlay(catalog, raw),
      /SESSION_SEARCH_SOURCE_WATERMARKS_INVALID/,
    );
  }
});


const snapshot = {
  source_scope: 'GEMINI_APPS_ACTIVITY',
  observed_through: 450,
  basis: 'provider_takeout_request_receipt',
  accepted_set_sha256: 'e'.repeat(64),
};

test('source snapshot overlay is additive and distinct from source watermark', () => {
  const geminiCatalog = [
    { shard_id: 'gemini-0001', provider: 'gemini', generation_id: 'f'.repeat(64), url: 'https://g.test/search' },
  ];
  const applied = applySourceSnapshotOverlay(
    geminiCatalog,
    JSON.stringify({ gemini: snapshot }),
  );
  assert.deepEqual(applied[0].source_snapshot, snapshot);
  assert.equal(Object.hasOwn(applied[0], 'source_watermark'), false);
  assert.equal(Object.hasOwn(geminiCatalog[0], 'source_snapshot'), false);
});

test('source snapshot overlay fails closed on strengthened or malformed metadata', () => {
  const geminiCatalog = [
    { shard_id: 'gemini-0001', provider: 'gemini', generation_id: 'f'.repeat(64), url: 'https://g.test/search' },
  ];
  const bad = [
    JSON.stringify({ gemini: { ...snapshot, status: 'PROVIDER_REPORTED_NO_KNOWN_GAPS' } }),
    JSON.stringify({ gemini: { ...snapshot, source_scope: 'ALL_GEMINI_HISTORY' } }),
    JSON.stringify({ gemini: { ...snapshot, observed_through: null } }),
    JSON.stringify({ gemini: { ...snapshot, accepted_set_sha256: 'bad' } }),
  ];
  for (const raw of bad) {
    assert.throws(
      () => applySourceSnapshotOverlay(geminiCatalog, raw),
      /SESSION_SEARCH_SOURCE_SNAPSHOTS_INVALID/,
    );
  }
});
