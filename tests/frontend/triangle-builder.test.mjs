import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import {
  build,
  implementationIdentity,
} from '../../tooling/build-triangle-daily.mjs';
import {
  analyzeTriangles,
  defaultTriangleParams,
} from '../../src/lib/triangles.ts';

const hash = (raw) => createHash('sha256').update(raw).digest('hex');

async function frozenInput(t) {
  const folder = await mkdtemp(path.join(tmpdir(), 'triangle-builder-test-'));
  t.after(() => rm(folder, { recursive: true, force: true }));
  const input = path.join(folder, 'input'),
    output = path.join(folder, 'output');
  await mkdir(input);
  const dates = [],
    date = new Date('2026-01-01T00:00:00Z');
  while (dates.length < 150) {
    if (date.getUTCDay() !== 0 && date.getUTCDay() !== 6)
      dates.push(date.toISOString().slice(0, 10));
    date.setUTCDate(date.getUTCDate() + 1);
  }
  const bars = [];
  for (let index = 0; index < dates.length; index++) {
    const day = dates[index],
      close = index < 120 ? 150 - index * 0.5 : 90 + (index - 120) * 1.5;
    const bar = {
      kind: 'bar',
      date: day,
      open: close,
      high: close + 0.2,
      low: close - 0.2,
      close,
    };
    bars.push(bar);
    const rows = [
      {
        ...bar,
        code: '000001',
        name: 'Fictional current',
        market: 'SZ',
        priceStatus: 'available',
      },
    ];
    if (index < 10)
      rows.push({ ...rows[0], code: '000002', name: 'Fictional historical' });
    await writeFile(
      path.join(input, day + '.json'),
      JSON.stringify({
        date: day,
        generatedAt: day + 'T17:00:00+08:00',
        scope: 'full',
        priceDataComplete: true,
        pendingCount: 0,
        rows,
      }),
    );
  }
  const manifest = {
    dates: dates.map((day) => ({
      date: day,
      file: day + '.json',
      generatedAt: day + 'T17:00:00+08:00',
    })),
    tradingDates: dates,
    generatedAt: dates.at(-1) + 'T17:00:00+08:00',
    universeTotal: 1,
  };
  await writeFile(path.join(input, 'manifest.json'), JSON.stringify(manifest));
  await writeFile(
    path.join(input, 'stocks.json'),
    JSON.stringify({
      rows: [{ code: '000001', name: 'Fictional current', market: 'SZ' }],
    }),
  );
  return { input, output, bars, dates, manifest };
}

test('server screen shares exact default engine and full-history bundles retain historical-only stocks', async (t) => {
  const fixture = await frozenInput(t);
  const publication = await build({
    ...fixture,
    datasetId: 'abcdef0123456789',
    asOf: fixture.dates.at(-1),
  });
  const screenRaw = await readFile(path.join(fixture.output, 'screening.json'));
  const screen = JSON.parse(screenRaw);
  const expected = analyzeTriangles({
    bars: fixture.bars.slice(-140),
    params: defaultTriangleParams,
    asOf: publication.as_of,
  });
  assert.deepEqual(
    screen.rows[0].formations,
    expected.formations.filter(
      (formation) =>
        formation.qualified &&
        ['active', 'observing'].includes(formation.state.kind),
    ),
  );
  assert.equal(screen.rows.length, 1);
  assert.equal(screen.sessions.length, 140);
  assert.equal(publication.detail_sessions.length, 150);
  assert.equal(publication.screening.sha256, hash(screenRaw));
  assert.deepEqual(
    publication.history_only_securities.map((security) => security.code),
    ['000002'],
  );
  const bundle = JSON.parse(
    await readFile(path.join(fixture.output, publication.bundles[0].file)),
  );
  assert.equal(bundle.rows.length, 2);
  assert.deepEqual(
    bundle.rows.find((row) => row.security.code === '000001').bars,
    fixture.bars,
  );
  assert.equal(
    bundle.rows.find((row) => row.security.code === '000002').bars.at(-1)
      .reason,
    'missing-row',
  );
});

test('uncommitted date replacement refuses a server publication', async (t) => {
  const fixture = await frozenInput(t);
  const filename = path.join(fixture.input, fixture.dates[20] + '.json');
  const day = JSON.parse(await readFile(filename));
  day.generatedAt = fixture.dates[20] + 'T18:00:00+08:00';
  await writeFile(filename, JSON.stringify(day));
  await assert.rejects(
    build({
      ...fixture,
      datasetId: 'abcdef0123456789',
      asOf: fixture.dates.at(-1),
    }),
    /FROZEN_SOURCE_READ_FAILED/,
  );
});

test('published rule identity is the shared engine source hash', async () => {
  const identity = await implementationIdentity();
  assert.equal(
    identity.rule_sha256,
    hash(
      await readFile(new URL('../../src/lib/triangles.ts', import.meta.url)),
    ),
  );
  assert.deepEqual(identity.params, defaultTriangleParams);
});
