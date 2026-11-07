import test from 'node:test';
import assert from 'node:assert/strict';
import {
  boundaryAt,
  locateSpace,
  nearestReference,
  observeVideoDay,
  parseVideoManifest,
  parseVideoFrame,
  relate,
  verifyVideoStock,
  videoScenarios,
  videoStocks,
} from '../../src/lib/video-space.ts';

const ruler = { H: 200, L: 100 };
const manifest = {
  dataset: '1234567890abcdef',
  sourceDataset: '1234567890abcdef',
  asOf: '2026-11-05',
};
const bar = (date, open = '80', close = '90') => ({
  date,
  open,
  high: String(Math.max(+open, +close) + 5),
  low: String(Math.min(+open, +close) - 5),
  close,
  volume_shares: '10',
});
function input() {
  return {
    schema: 'stock.chips.market.stock.v1',
    security_id: '300502',
    dataset_id: manifest.dataset,
    as_of: manifest.asOf,
    source: {
      provider: 'THS_UNADJUSTED',
      adjustment: '00_UNADJUSTED',
      url: 'https://d.10jqka.com.cn/v6/line/hs_300502/00/last10000.js',
      sha256: 'a'.repeat(64),
    },
    history: [bar('2026-11-04'), bar('2026-11-05')],
  };
}
function stock(code = '300502') {
  const endpoints = [
    ...new Set(
      videoScenarios
        .filter((s) => s.code === code)
        .flatMap((s) => [s.high.date, s.low.date]),
    ),
  ].sort();
  const frozen = endpoints.map((date) => ({
    ...bar(date),
    high: '200',
    low: '100',
    open: '150',
    close: '150',
  }));
  frozen.push(bar('2026-10-01'));
  return {
    code,
    asOf: '2026-11-05',
    dataset: manifest.dataset,
    sourceDataset: manifest.sourceDataset,
    frozen: { code, history: frozen },
    history: [
      bar('2026-11-02', '85', '84'),
      bar('2026-11-03', '88', '95'),
      bar('2026-11-04', '100', '90'),
      bar('2026-11-05', '89', '91'),
    ],
  };
}
const primary = 'XY_SUPP_PRIMARY';
const auto = { kind: 'AUTO_PREVIOUS_CLOSE' };
function ready(value) {
  assert.equal(value.kind, 'ready');
  return value;
}

test('registry contains exactly three stocks and eleven fixed candidates with one default each', () => {
  assert.equal(videoStocks.length, 3);
  assert.equal(videoScenarios.length, 11);
  assert.equal(new Set(videoScenarios.map((s) => s.id)).size, 11);
  for (const s of videoStocks)
    assert.equal(
      videoScenarios.filter((c) => c.code === s.code && c.primary).length,
      1,
    );
});
test('LOG boundaries and half grids preserve ratios above and below the original ruler', () => {
  assert.equal(boundaryAt(ruler, 0), 200);
  assert.equal(boundaryAt(ruler, 1), 100);
  assert.equal(boundaryAt(ruler, -1), 400);
  assert.ok(Math.abs(boundaryAt(ruler, 0.5) ** 2 - 200 * 100) < 1e-8);
  const loc = locateSpace(ruler, 75);
  assert.equal(loc.upper.k, 1);
  assert.equal(loc.lower.k, 2);
  assert.equal(loc.middle.k, 1.5);
  assert.equal(loc.onBoundary, false);
  const exact = locateSpace(ruler, 100);
  assert.equal(exact.onBoundary, true);
  assert.equal(exact.adjacent.upper.k, 0);
  assert.equal(exact.upper.k, 1);
});
test('strict side and inclusive engineering band are separate observations', () => {
  assert.deepEqual(relate(100, 100), {
    side: 'ON',
    inBand: true,
    distancePercent: 0,
  });
  assert.equal(relate(100.5, 100).side, 'ABOVE');
  assert.equal(relate(100.5, 100).inBand, true);
  assert.equal(relate(99.5, 100).inBand, true);
  assert.equal(relate(100.5001, 100).inBand, false);
  assert.equal(relate(99.4999, 100).side, 'BELOW');
});
test('previous-close reference supports negative k and takes the higher price at LOG ties', () => {
  assert.equal(nearestReference(ruler, 400).k, -1);
  assert.equal(nearestReference(ruler, boundaryAt(ruler, 0.25)).k, 0);
  assert.equal(nearestReference(ruler, boundaryAt(ruler, -0.25)).k, -0.5);
});
test('every stock candidate reads its own frozen endpoints and shares the reference for O and C', () => {
  for (const s of videoScenarios) {
    const o = ready(observeVideoDay(stock(s.code), s.id, '2026-11-04', auto));
    assert.deepEqual(o.ruler, ruler);
    assert.deepEqual(o.reference.open, relate(+o.bar.open, o.reference.price));
    assert.deepEqual(
      o.reference.close,
      relate(+o.bar.close, o.reference.price),
    );
    assert.ok(
      o.spaceLines.every(
        (l) =>
          l.rangeIncludes === (+o.bar.low <= l.price && +o.bar.high >= l.price),
      ),
    );
  }
});
test('the current O/H/L/C and future history cannot move the automatic reference', () => {
  const s = stock(),
    base = ready(observeVideoDay(s, primary, '2026-11-04', auto));
  for (const field of ['open', 'high', 'low', 'close']) {
    const altered = {
      ...s,
      history: s.history.map((b) =>
        b.date === '2026-11-04' ? { ...b, [field]: '9999' } : b,
      ),
    };
    assert.equal(
      ready(observeVideoDay(altered, primary, '2026-11-04', auto)).reference.k,
      base.reference.k,
    );
  }
  const future = {
    ...s,
    history: [
      ...s.history.map((b) =>
        b.date > '2026-11-04' ? bar(b.date, '999', '999') : b,
      ),
      bar('2026-11-06', '1', '1'),
    ],
  };
  assert.equal(
    ready(observeVideoDay(future, primary, '2026-11-04', auto)).reference.price,
    base.reference.price,
  );
  assert.equal(base.previous.date, '2026-11-03');
});
test('fixed k stays fixed over dates and missing previous close is pending only in automatic mode', () => {
  const s = stock(),
    fixed = { kind: 'FIXED', k: -1.5 };
  for (const date of ['2026-11-02', '2026-11-03', '2026-11-04'])
    assert.equal(
      ready(observeVideoDay(s, primary, date, fixed)).reference.k,
      -1.5,
    );
  assert.match(
    observeVideoDay(s, primary, '2026-11-02', auto).reason,
    /前一根/,
  );
  assert.equal(
    observeVideoDay(s, primary, '2026-11-04', { kind: 'FIXED', k: 0.2 }).kind,
    'pending',
  );
});
test('unformed scenarios, absent dates and wrong-stock scenarios never emit price judgments', () => {
  assert.match(
    observeVideoDay(stock(), primary, '2026-08-18', auto).reason,
    /尚未形成/,
  );
  assert.equal(
    observeVideoDay(stock(), primary, '2026-12-01', auto).kind,
    'pending',
  );
  assert.equal(
    observeVideoDay(stock('300308'), primary, '2026-11-04', auto).kind,
    'pending',
  );
});
test('chips manifest validates identity and real calendar dates without requiring V4 geometry', () => {
  assert.deepEqual(
    parseVideoManifest({
      schema: 'stock.chips.market.manifest.v1',
      dataset_id: manifest.dataset,
      as_of: manifest.asOf,
    }),
    manifest,
  );
  for (const delta of [
    { schema: 'unknown' },
    { dataset_id: '../other' },
    { as_of: '2026-02-30' },
  ])
    assert.throws(() =>
      parseVideoManifest({
        schema: 'stock.chips.market.manifest.v1',
        dataset_id: manifest.dataset,
        as_of: manifest.asOf,
        ...delta,
      }),
    );
});
test('narrow chips parser preserves decimal strings and rejects identity, source and history conflicts', () => {
  assert.equal(
    parseVideoFrame(input(), '300502', manifest).history[0].open,
    '80',
  );
  for (const delta of [
    { schema: 'unknown' },
    { security_id: '300308' },
    { dataset_id: 'abcdef1234567890' },
    { as_of: '2026-11-04' },
    { source: {} },
    { history: [] },
  ])
    assert.throws(() =>
      parseVideoFrame({ ...input(), ...delta }, '300502', manifest),
    );
  for (const delta of [
    { provider: 'OTHER' },
    { adjustment: 'qfq' },
    { sha256: 'abc' },
    { url: 'https://example.com/' },
  ])
    assert.throws(() =>
      parseVideoFrame(
        { ...input(), source: { ...input().source, ...delta } },
        '300502',
        manifest,
      ),
    );
  for (const history of [
    [bar('2026-11-05'), bar('2026-11-04')],
    [bar('2026-11-05'), bar('2026-11-05')],
    [bar('2026-11-03')],
    [{ ...bar('2026-11-05'), low: '95' }],
    [{ ...bar('2026-11-05'), open: '0' }],
    [{ ...bar('2026-11-05'), volume_shares: '-1' }],
    [{ ...bar('2026-11-05'), close: undefined }],
  ])
    assert.throws(() =>
      parseVideoFrame({ ...input(), history }, '300502', manifest),
    );
});
test('frozen history cannot claim the registered source fingerprint with changed contents', async () => {
  const s = input();
  const frozen = {
    ...s,
    schema: 'stock.golden-projection.daily.stock.v1',
    code: '300502',
    dataset_id: '31c3b953170dd239',
    source_dataset_id: 'b6b7196f317c4f9f',
    as_of: '2026-11-04',
    source: {
      provider: 'THS_UNADJUSTED',
      key: 'data/chips/daily/b6b7196f317c4f9f/300502.json',
      last_bar_date: '2026-11-04',
      sha256: videoStocks[0].sourceSha,
    },
    history: [bar('2026-11-04')],
  };
  await assert.rejects(
    verifyVideoStock({
      code: '300502',
      frozenInput: frozen,
      latestInput: s,
      manifest,
    }),
    /指纹/,
  );
});

test('half ruler inherits parent boundaries after a completed break and keeps one reference for O and C', async () => {
  const { observeVideoLayers } = await import('../../src/lib/video-space.ts');
  const s = stock();
  const before = observeVideoLayers(s, '2026-11-02', auto, {
    parentId: primary,
    scale: 'HALF',
  });
  assert.equal(before.observation.kind, 'pending');
  const result = observeVideoLayers(s, '2026-11-03', auto, {
    parentId: primary,
    scale: 'HALF',
  });
  assert.equal(result.half.kind, 'available');
  const half = ready(result.observation),
    parent = ready(result.parent);
  assert.equal(half.source.kind, 'HALF');
  assert.equal(half.source.endpoints.kind, 'PARENT_BOUNDARY');
  for (const j of [-1, 0, 0.5, 1, 2])
    assert.ok(
      Math.abs(boundaryAt(half.ruler, j) - boundaryAt(parent.ruler, j / 2)) <
        1e-8,
    );
  assert.deepEqual(
    half.reference.open,
    relate(+half.bar.open, half.reference.price),
  );
  assert.deepEqual(
    half.reference.close,
    relate(+half.bar.close, half.reference.price),
  );
  assert.deepEqual(
    ready(
      observeVideoLayers(s, '2026-11-03', auto, {
        parentId: primary,
        scale: 'PARENT',
      }).observation,
    ).ruler,
    parent.ruler,
  );
});

test('an archived selected V1 ruler remains its own root context', async () => {
  const { observeVideoLayers } = await import('../../src/lib/video-space.ts');
  const history = Array.from({ length: 55 }, (_, i) => {
    const date = new Date(Date.UTC(2025, 0, i + 1)).toISOString().slice(0, 10);
    const base = i < 22 ? 95 : 120;
    return {
      date,
      open: String(base),
      high: String(base + 1),
      low: String(base - 1),
      close: String(base),
      volume_shares: '1',
    };
  });
  for (const [i, kind, price] of [
    [3, 'H', 100],
    [7, 'L', 90],
    [11, 'H', 100],
    [15, 'L', 90],
    [25, 'H', 130],
    [29, 'L', 110],
    [33, 'H', 130],
    [37, 'L', 110],
  ]) {
    history[i] =
      kind === 'H'
        ? {
            ...history[i],
            open: String(price - 1),
            close: String(price - 1),
            high: String(price),
          }
        : {
            ...history[i],
            open: String(price + 1),
            close: String(price + 1),
            low: String(price),
          };
  }
  for (const i of [20, 21])
    history[i] = { ...history[i], open: '102', close: '102', high: '103' };
  const s = {
    ...stock(),
    history,
    autoHistory: { kind: 'ready', history, historyStartDate: history[0].date },
  };
  const date = history.at(-1).date;
  const current = observeVideoLayers(s, date, auto, {
    parentId: 'CURRENT_ROOT',
    scale: 'PARENT',
  });
  assert.equal(current.family.root.versions.length, 2);
  const archived = current.family.root.versions[0];
  const selected = observeVideoLayers(s, date, auto, {
    parentId: archived.id,
    scale: 'PARENT',
  });
  assert.equal(ready(selected.parent).source.version.id, archived.id);
  assert.equal(ready(selected.rootContext).source.version.id, archived.id);
  assert.notEqual(archived.id, current.family.root.version.id);
});

test('selecting a structural wave retains fixed rulers and never backfills its formation', async () => {
  const { observeVideoLayers } = await import('../../src/lib/video-space.ts');
  const history = Array.from({ length: 24 }, (_, i) => ({
    date: new Date(Date.UTC(2025, 2, i + 1)).toISOString().slice(0, 10),
    open: '60',
    high: '61',
    low: '59',
    close: '60',
    volume_shares: '1',
  }));
  history[3] = { ...history[3], high: '100', open: '99', close: '99' };
  history[7] = { ...history[7], low: '40', open: '41', close: '41' };
  history[11] = { ...history[11], high: '90', open: '89', close: '89' };
  const original = stock();
  const s = {
    ...original,
    history,
    autoHistory: { kind: 'ready', history, historyStartDate: history[0].date },
  };
  const date = history[14].date;
  const catalog = observeVideoLayers(s, date, auto, {
    parentId: 'CURRENT_ROOT',
    scale: 'PARENT',
  });
  const wave = catalog.options.find((item) => item.id.startsWith('wave:'));
  assert.ok(wave);
  const selected = observeVideoLayers(s, date, auto, {
    parentId: wave.id,
    scale: 'PARENT',
  });
  assert.equal(ready(selected.observation).source.kind, 'WAVE');
  assert.equal(ready(selected.observation).source.wave.reasons.length, 2);
  assert.equal(
    observeVideoLayers(s, history[13].date, auto, {
      parentId: wave.id,
      scale: 'PARENT',
    }).observation.kind,
    'pending',
  );
  assert.deepEqual(s.frozen, original.frozen);
  assert.equal(videoScenarios.length, 11);
});
