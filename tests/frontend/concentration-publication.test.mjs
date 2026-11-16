import test from 'node:test';
import assert from 'node:assert/strict';
import {
  concentrationPublicationPath,
  concentrationPublicationReader,
  parseConcentrationPublication,
} from '../../src/lib/concentration-publication.ts';
import { concentrationModel, concentrationRule } from '../../src/lib/concentration-screen.ts';
import { startPublicationFollow } from '../../src/lib/publication-follow.ts';

const flush = () => new Promise(resolve => setImmediate(resolve));
const sessions = Array.from({ length: 21 }, (_, index) => `2026-10-${String(index + 10).padStart(2, '0')}`);

function batch(dataset = '0123456789abcdef', generated = '2026-10-30T10:00:00Z') {
  const source = {
    schema: 'stock.chips.market.manifest.v1', dataset_id: dataset, active_slot: dataset,
    as_of: sessions.at(-1), generated_at: generated, universe_total: 1, scanned_count: 1,
    coefficient: 2, model_version: concentrationModel, universe_coverage: 'SITE_CATALOG_UNVERIFIED',
  };
  const pointer = {
    schema: 'stock.chips.concentration.publication.v1', source,
    dataset_id: dataset, as_of: source.as_of, generated_at: generated,
    payload_key: `data/chips/daily/${dataset}/concentration-two-v1.json`,
    input_fingerprint: 'f'.repeat(64), payload_sha256: 'a'.repeat(64),
  };
  const scan = {
    schema: 'stock.chips.concentration.two.v1', rule_version: concentrationRule,
    dataset_id: dataset, active_slot: dataset, as_of: source.as_of,
    coefficient: 2, model_version: concentrationModel,
    window_policy: 'EACH_DATE_INDEPENDENT_TRAILING_210_OBSERVED_BARS',
    sessions, streak_sessions: sessions, universe_total: 1, processed_count: 1,
    source_universe_coverage: source.universe_coverage, input_fingerprint: pointer.input_fingerprint,
    rows: [{
      security_id: '999999', name: 'INVENTED', close_cny: '10',
      values: [...Array(20).fill(10), 11], point_reasons: {}, short_history_days: 0,
      streak_days: 0, streak_start_session: sessions.at(-1),
      streak_boundary: {
        kind: 'INCREASE', previous_session: sessions.at(-2), previous_value: 10,
        anchor_session: sessions.at(-1), anchor_value: 11,
      }, groups: { 20: 'FAIL', LONG: 'FAIL' },
    }],
    groups: { 20: { PASS: 0, FAIL: 1, UNKNOWN: 0 }, LONG: { PASS: 0, FAIL: 1, UNKNOWN: 0 } },
  };
  return { source, pointer, scan };
}

function feed(initial = batch()) {
  const state = { pointer: initial.pointer, main: initial.source, reads: [], payloads: new Map([[initial.pointer.payload_key, initial.scan]]) };
  const reader = concentrationPublicationReader(async path => {
    state.reads.push(path);
    if (path === concentrationPublicationPath) return structuredClone(state.pointer);
    if (path === '/data/chips/daily/manifest.json') return structuredClone(state.main);
    const result = state.payloads.get(path.slice(1));
    if (!result) throw Error('HTTP 503: new scan has no concentration result');
    return structuredClone(result);
  });
  return { state, reader };
}

function follow(reader, published, errors) {
  return startPublicationFollow({
    readStatus: reader.readManifest, readManifest: reader.readManifest, readPayload: reader.readPayload,
    onStatus: () => undefined, onPublication: (manifest, scan) => published.push({ manifest, scan }),
    onStatusError: error => { if (error) errors.push(error); },
    onPublicationError: error => { if (error) errors.push(error); },
    isRunning: () => false, publishedDataset: value => value.dataset_id,
    isVisible: () => true, listenVisibility: () => () => undefined,
    setTimer: () => 0, clearTimer: () => undefined,
  });
}

test('fresh load uses the last complete publication while the main scan has advanced', async () => {
  const { state, reader } = feed();
  state.main = batch('1111111111111111', '2026-10-30T11:00:00Z').source;
  const signal = new AbortController().signal;
  const publication = await reader.readManifest(signal);
  const scan = await reader.readPayload(publication, signal);
  assert.equal(scan.dataset_id, '0123456789abcdef');
  assert.equal(publication.generated_at, '2026-10-30T10:00:00Z');
  assert.ok(state.reads.every(path => !path.includes('1111111111111111')));
  assert.ok(!state.reads.includes('/data/chips/daily/manifest.json'));
});

test('an open page keeps the previous complete result and switches after publication', async () => {
  const { state, reader } = feed();
  const published = [], errors = [];
  const task = follow(reader, published, errors);
  await flush();
  assert.equal(published.length, 1);
  const next = batch('1111111111111111', '2026-10-30T11:00:00Z');
  state.main = next.source;
  task.refresh(); await flush();
  assert.equal(published.length, 1);
  assert.equal(state.reads.filter(path => path.endsWith('/concentration-two-v1.json')).length, 1);
  state.payloads.set(next.pointer.payload_key, next.scan);
  state.pointer = next.pointer;
  task.refresh(); await flush();
  assert.deepEqual(published.map(value => value.scan.dataset_id), ['0123456789abcdef', '1111111111111111']);
  assert.equal(errors.length, 0);
  task.stop();
});

test('a failed new result keeps the displayed publication and succeeds on a later refresh', async () => {
  const { state, reader } = feed();
  const published = [], errors = [];
  const task = follow(reader, published, errors);
  await flush();
  const next = batch('1111111111111111', '2026-10-30T11:00:00Z');
  state.pointer = next.pointer;
  state.payloads.set(next.pointer.payload_key, { ...next.scan, rows: [] });
  task.refresh(); await flush();
  assert.equal(published.length, 1);
  assert.ok(errors.length > 0);
  state.payloads.set(next.pointer.payload_key, next.scan);
  task.refresh(); await flush();
  assert.equal(published.at(-1).scan.dataset_id, next.source.dataset_id);
  task.stop();
});

test('a publication change during the payload read cannot mix two batches', async () => {
  const initial = batch(), next = batch('1111111111111111', '2026-10-30T11:00:00Z');
  const reader = concentrationPublicationReader(async path =>
    path === concentrationPublicationPath ? next.pointer : initial.scan);
  await assert.rejects(reader.readPayload(parseConcentrationPublication(initial.pointer), new AbortController().signal), /正在发布/);
});

test('publication metadata binds the exact private result and full source identity', () => {
  const { pointer } = batch();
  for (const changes of [
    { schema: 'WRONG' }, { dataset_id: '1111111111111111' }, { as_of: '2026-11-02' },
    { generated_at: '2026-10-30T11:00:00Z' }, { payload_key: '../private.json' },
    { input_fingerprint: '' }, { payload_sha256: 'not-a-hash' },
  ]) assert.throws(() => parseConcentrationPublication({ ...pointer, ...changes }));
});
