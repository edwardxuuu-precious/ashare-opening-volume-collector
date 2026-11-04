import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import {
  createTriangleDailyDetailReader,
  filterTriangleDailyRows,
  loadTriangleDailyManifest,
  loadTriangleDailyScreen,
  parseTriangleDailyBundle,
  parseTriangleDailyManifest,
  parseTriangleDailyScreen,
  parseTriangleDailyStatus,
  triangleDailyRuleSha256,
} from '../../src/lib/triangle-publication.ts';
import { analyzeTriangles } from '../../src/lib/triangles.ts';
import {
  trianglePublicationFixture,
  jsonBytes,
} from './fixtures/triangle-publication.mjs';

test('the published rule identity matches the exact engine used for historical detail', async () => {
  const source = await readFile(
    new URL('../../src/lib/triangles.ts', import.meta.url),
  );
  assert.equal(
    createHash('sha256').update(source).digest('hex'),
    triangleDailyRuleSha256,
  );
});
test('the daily screen and all-history bundle preserve real engine results with different windows', () => {
  const fixture = trianglePublicationFixture();
  const manifest = parseTriangleDailyManifest(fixture.manifest);
  const screen = parseTriangleDailyScreen(fixture.screen, manifest);
  assert.equal(manifest.sessions.length, 140);
  assert.equal(manifest.detail_sessions.length, 180);
  assert.equal(filterTriangleDailyRows(screen, 'golden', 5).length, 1);
  assert.equal(filterTriangleDailyRows(screen, 'death', 5).length, 2);
  const symbols = parseTriangleDailyBundle(
    fixture.bundle,
    manifest,
    'bundle-000.json',
  );
  const cycle = symbols.find((row) => row.security.code === '000002');
  const analysis = analyzeTriangles({
    bars: cycle.bars,
    params: manifest.params,
    asOf: manifest.as_of,
  });
  assert.deepEqual(
    new Set(
      analysis.formations
        .filter((formation) => formation.qualified)
        .map((formation) => formation.direction),
    ),
    new Set(['golden', 'death']),
  );
  assert.ok(
    analysis.formations.some(
      (formation) =>
        formation.qualified &&
        !['active', 'observing'].includes(formation.state.kind),
    ),
  );
  assert.equal(
    screen.rows.find((row) => row.security.code === '000003').availability.kind,
    'insufficient',
  );
});
test('a default publication read uses only the manifest and one immutable screen', async () => {
  const fixture = trianglePublicationFixture(),
    requests = [];
  const fetcher = async (url) => {
    requests.push(String(url));
    return new Response(
      jsonBytes(
        String(url).endsWith('/manifest.json')
          ? fixture.manifest
          : fixture.screen,
      ),
    );
  };
  const options = {
    dataBase: 'https://private.example',
    signal: new AbortController().signal,
    fetcher,
  };
  const manifest = await loadTriangleDailyManifest(options);
  await loadTriangleDailyScreen(manifest, options);
  assert.deepEqual(
    requests.map((url) => new URL(url).pathname),
    [
      '/data/triangles/daily/manifest.json',
      '/data/triangles/daily/abcdef0123456789/screening.json',
    ],
  );
});
test('detail reads cache only a verified version-bound bundle and preserve every saved bar', async () => {
  const fixture = trianglePublicationFixture(),
    manifest = parseTriangleDailyManifest(fixture.manifest);
  const publication = {
    manifest,
    screen: parseTriangleDailyScreen(fixture.screen, manifest),
  };
  const read = createTriangleDailyDetailReader(publication),
    requests = [];
  const options = {
    dataBase: 'https://private.example',
    signal: new AbortController().signal,
    fetcher: async (url) => {
      requests.push(String(url));
      return new Response(jsonBytes(fixture.bundle));
    },
  };
  assert.equal((await read('000002', options)).bars.length, 180);
  assert.equal((await read('000003', options)).bars[0].kind, 'gap');
  assert.equal(requests.length, 1);
  await assert.rejects(read('999999', options), /不在当前发布名单/);
});
test('hash mismatch and a foreign dataset never become a current screen', async () => {
  const fixture = trianglePublicationFixture(),
    manifest = parseTriangleDailyManifest(fixture.manifest);
  const corrupted = jsonBytes(fixture.screen)
    .toString()
    .replace('金三角样例', '坏三角样例');
  await assert.rejects(
    loadTriangleDailyScreen(manifest, {
      dataBase: '',
      signal: new AbortController().signal,
      fetcher: async () => new Response(corrupted),
    }),
    /校验失败/,
  );
  assert.throws(
    () =>
      parseTriangleDailyScreen(
        { ...fixture.screen, dataset_id: '0000000000000000' },
        manifest,
      ),
    /版本不一致/,
  );
});
test('malformed identities, future rules, traversal, incomplete universes and inconsistent history are rejected', () => {
  const fixture = trianglePublicationFixture();
  for (const modify of [
    (value) => {
      value.rule_sha256 = '0'.repeat(64);
    },
    (value) => {
      value.bundles[0].file = '../screening.json';
    },
    (value) => {
      value.universe_total++;
    },
    (value) => {
      value.bundles[0].codes.push('000001');
    },
    (value) => {
      value.detail_sessions.splice(50, 1);
    },
    (value) => {
      value.params.minDeclinePct = 5;
    },
  ]) {
    const value = structuredClone(fixture.manifest);
    modify(value);
    assert.throws(() => parseTriangleDailyManifest(value));
  }
  const manifest = parseTriangleDailyManifest(fixture.manifest);
  assert.throws(() =>
    parseTriangleDailyScreen(
      { ...fixture.screen, rows: fixture.screen.rows.slice(1) },
      manifest,
    ),
  );
});
test('historical OHLC, missing bars, duplicates and cross-dataset details are validated at the boundary', () => {
  const fixture = trianglePublicationFixture(),
    manifest = parseTriangleDailyManifest(fixture.manifest);
  for (const modify of [
    (value) => {
      value.rows[0].bars.pop();
    },
    (value) => {
      value.rows[0].bars[0].high = 0;
    },
    (value) => {
      value.rows[0].bars[0].date = '2026-12-31';
    },
    (value) => {
      value.rows[0].security.code = value.rows[1].security.code;
    },
    (value) => {
      value.dataset_id = '0000000000000000';
    },
  ]) {
    const value = structuredClone(fixture.bundle);
    modify(value);
    assert.throws(() =>
      parseTriangleDailyBundle(value, manifest, 'bundle-000.json'),
    );
  }
});
test('a failed status remains a failed update with its last committed day', () => {
  const fixture = trianglePublicationFixture();
  const status = parseTriangleDailyStatus({
    ...fixture.status,
    state: 'FAILED',
    message: 'Source pending.',
  });
  assert.equal(status.state, 'FAILED');
  assert.equal(status.last_committed_date, fixture.manifest.as_of);
  assert.throws(() =>
    parseTriangleDailyStatus({ ...fixture.status, state: 'PARTIAL_SUCCESS' }),
  );
});
