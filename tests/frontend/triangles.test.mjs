import test from 'node:test';
import assert from 'node:assert/strict';
import {
  analyzeTriangles,
  defaultTriangleParams,
  parameterFields,
  validateTriangleParams,
} from '../../src/lib/triangles.ts';
import {
  triangleFixtureBars,
  triangleFixtureAsOf,
} from './fixtures/triangles.mjs';

function dateAt(index) {
  const day = new Date('2026-06-01T00:00:00Z');
  let session = 0;
  for (;;) {
    if (day.getUTCDay() !== 0 && day.getUTCDay() !== 6) {
      if (session === index) return day.toISOString().slice(0, 10);
      session++;
    }
    day.setUTCDate(day.getUTCDate() + 1);
  }
}
function makeBars(closes) {
  return closes.map((close, index) => ({
    kind: 'bar',
    date: dateAt(index),
    open: close,
    high: close + 0.2,
    low: close - 0.2,
    close,
  }));
}
function goldenBars(zones = true) {
  const bars = structuredClone(triangleFixtureBars.slice(0, 89));
  if (!zones) {
    for (const index of [30, 45, 60]) bars[index].low = bars[index].close - 0.2;
    for (const index of [35, 50, 65])
      bars[index].high = bars[index].close + 0.2;
  }
  return bars;
}
function deathBars() {
  return makeBars([
    ...Array.from({ length: 80 }, (_, index) => 50 + index * 0.8),
    ...Array.from({ length: 9 }, (_, index) => 113 - index * 1.5),
  ]);
}
function append(bars, close, low = close - 0.2) {
  bars.push({
    kind: 'bar',
    date: dateAt(bars.length),
    open: close,
    high: close + 0.2,
    low,
    close,
  });
  return bars;
}
function analyze(
  bars,
  overrides = {},
  asOf = bars.at(-1)?.date ?? '2026-11-04',
) {
  return analyzeTriangles({
    bars,
    params: { ...defaultTriangleParams, ...overrides },
    asOf,
  });
}
function firstGolden(result) {
  const formation = result.formations.find((row) => row.direction === 'golden');
  assert.ok(formation, 'a completed golden triangle must remain observable');
  return formation;
}

test('default rules find real golden and death triangles with the actual three cross dates', () => {
  const golden = firstGolden(analyze(goldenBars(false)));
  assert.equal(golden.qualified, true);
  assert.equal(golden.startedAt, dateAt(84));
  assert.equal(golden.formedAt, dateAt(88));
  assert.deepEqual(
    golden.crosses.map((cross) => cross.date),
    [dateAt(84), dateAt(86), dateAt(88)],
  );
  assert.equal(new Set(golden.crosses.map((cross) => cross.pair)).size, 3);
  assert.equal(golden.state.kind, 'active');
  const death = analyze(deathBars()).formations.find(
    (row) => row.direction === 'death',
  );
  assert.ok(death);
  assert.equal(death.qualified, true);
  assert.equal(death.state.kind, 'observing');
  assert.deepEqual(
    death.crosses.map((cross) => cross.direction),
    ['down', 'down', 'down'],
  );
  assert.ok(death.pretrend.from.date < death.pretrend.to.date);
  assert.equal(death.pretrend.from.source, 'low');
  assert.equal(death.pretrend.to.source, 'high');
  assert.deepEqual(
    death.events.map((event) => event.kind),
    ['formed'],
  );
});

test('completed crosses with a weak decline remain a rejected raw triangle', () => {
  const bars = makeBars([
    ...Array.from({ length: 80 }, (_, index) => 100 - index * 0.05),
    ...Array.from({ length: 15 }, (_, index) => 96 + index * 0.3),
  ]);
  const formation = firstGolden(analyze(bars));
  assert.equal(formation.qualified, false);
  assert.equal(formation.state.kind, 'rejected');
  assert.ok(formation.pretrend.movePct < defaultTriangleParams.minDeclinePct);
  assert.match(formation.reasons.join('；'), /未达到/);
  assert.equal(formation.crosses.length, 3);
});

test('prior drawdown and advance use chronological anchors and never join a later extreme to an earlier opposite extreme', () => {
  const goldenInput = goldenBars(false);
  goldenInput[30].low = 10;
  goldenInput[83].high = 500;
  const golden = firstGolden(analyze(goldenInput, { minDeclinePct: 95 }));
  assert.equal(golden.pretrend.from.date, dateAt(24));
  assert.equal(golden.pretrend.to.date, dateAt(30));
  assert.ok(
    Math.abs(golden.pretrend.movePct - ((131 - 10) / 131) * 100) < 1e-8,
  );
  assert.equal(golden.qualified, false);
  const deathInput = deathBars();
  deathInput[30].high = 300;
  deathInput[83].low = 1;
  const death = analyze(deathInput, { minAdvancePct: 500 }).formations.find(
    (row) => row.direction === 'death',
  );
  assert.ok(death);
  assert.equal(death.pretrend.from.date, dateAt(24));
  assert.equal(death.pretrend.to.date, dateAt(30));
  assert.ok(death.pretrend.movePct < 500);
  assert.equal(death.qualified, false);
});

test('full configured lookbacks are required and never shortened to available history', () => {
  const result = analyze(goldenBars(), {
    pretrendSessions: 100,
    zoneLookbackSessions: 100,
  });
  const formation = firstGolden(result);
  assert.equal(formation.pretrend.complete, false);
  assert.equal(formation.pretrend.movePct, null);
  assert.equal(formation.pretrend.observedSessions, 84);
  assert.equal(formation.qualified, false);
  assert.equal(formation.support, null);
  assert.equal(formation.resistance, null);
  assert.match(formation.reasons.join('；'), /不缩短窗口/);
  assert.equal(result.availability.kind, 'insufficient');
});

test('overlapping prior slope and order windows do not falsely classify a qualified adjustable signal as insufficient', () => {
  const result = analyze(goldenBars(), {
    slopeSessions: 60,
    trendSessions: 30,
  });
  assert.equal(firstGolden(result).qualified, true);
  assert.equal(result.availability.kind, 'ready');
});

test('equality is a crossing origin and three same-day crossings carry no intraday order claim', () => {
  assert.equal(analyze(makeBars(Array(30).fill(100))).crosses.length, 0);
  for (const close of [110, 90]) {
    const bars = makeBars([...Array(30).fill(100), close]);
    const result = analyze(bars);
    assert.equal(result.crosses.length, 3);
    assert.equal(new Set(result.crosses.map((cross) => cross.date)).size, 1);
    assert.equal(result.formations.length, 1);
    assert.equal(result.formations[0].formedAt, bars[30].date);
    assert.equal(result.formations[0].qualified, false);
  }
});

test('a narrow formation window cannot borrow expired crossings', () => {
  const result = analyze(goldenBars(), { formationSessions: 2 });
  assert.equal(
    result.crosses.filter((cross) => cross.direction === 'up').length,
    3,
  );
  assert.equal(result.formations.length, 0);
});

test('a reverse crossing clears the candidate and later triangles use only fresh crossings', () => {
  const bars = makeBars([20, 19, 18, 17, 16, 16.8, 15, 16.4, 16.5]);
  const result = analyze(bars, {
    fastPeriod: 1,
    middlePeriod: 2,
    slowPeriod: 3,
    pretrendSessions: 3,
    trendSessions: 1,
    slopeSessions: 1,
    zoneLookbackSessions: 5,
    pivotRadius: 1,
    minDeclinePct: 0,
  });
  const formation = firstGolden(result);
  assert.equal(formation.startedAt, dateAt(7));
  assert.equal(formation.formedAt, dateAt(8));
  assert.ok(formation.crosses.every((cross) => cross.date >= dateAt(7)));
  assert.ok(
    result.crosses.some(
      (cross) => cross.date === dateAt(6) && cross.direction === 'down',
    ),
  );
});

test('all explicit market-data gaps end coverage, reset MA warmup and preserve history', async (t) => {
  for (const reason of [
    'not-traded',
    'missing-row',
    'missing-day',
    'invalid-price',
  ]) {
    await t.test(reason, () => {
      const bars = goldenBars();
      bars.push({ kind: 'gap', date: dateAt(89), reason });
      for (let i = 0; i < 20; i++) append(bars, 105 + i);
      const result = analyze(bars);
      const formation = firstGolden(result);
      assert.equal(formation.state.kind, 'coverage-ended');
      assert.equal(formation.events.at(-1).kind, 'coverage-ended');
      assert.equal(formation.events.at(-1).price, null);
      assert.ok(
        !formation.events.some((event) => event.kind === 'invalidated'),
      );
      assert.equal(result.points[89].slow, null);
      assert.ok(
        result.points.slice(90, 109).every((point) => point.slow === null),
      );
      assert.equal(result.points[109].slow, 114.5);
      assert.equal(result.availability.consecutiveBars, 20);
      assert.equal(formation.support.center, 80);
    });
  }
});

test('a gap during a pending triangle prevents reuse of its earlier crossings', () => {
  const bars = goldenBars().slice(0, 85);
  bars.push({ kind: 'gap', date: dateAt(85), reason: 'not-traded' });
  for (let i = 0; i < 30; i++) append(bars, 100 + i);
  const result = analyze(bars);
  assert.ok(result.crosses.some((cross) => cross.date === dateAt(84)));
  assert.equal(result.formations.length, 0);
});

test('the default fixture freezes real support and resistance with causally known touch evidence', () => {
  const formation = firstGolden(analyze(triangleFixtureBars));
  assert.equal(formation.support.center, 80);
  assert.equal(formation.resistance.center, 130);
  for (const zone of [formation.support, formation.resistance]) {
    assert.equal(zone.frozenAt, formation.startedAt);
    assert.ok(zone.knownAt < formation.startedAt);
    assert.equal(zone.touches.length, 3);
    assert.ok(
      zone.touches.every(
        (touch) =>
          touch.knownAt < formation.startedAt &&
          touch.occurredAt < touch.knownAt,
      ),
    );
    assert.ok(
      zone.touches.every(
        (touch) => touch.price >= zone.lower && touch.price <= zone.upper,
      ),
    );
    assert.ok(Object.isFrozen(zone) && Object.isFrozen(zone.touches));
  }
  assert.equal(triangleFixtureAsOf, triangleFixtureBars.at(-1).date);
  assert.equal(formation.thresholds.breakout.toFixed(5), '132.60975');
  assert.equal(formation.thresholds.retestRecovery.toFixed(2), '81.20');
  assert.equal(formation.thresholds.zoneInvalidation.toFixed(3), '78.406');
  assert.equal(formation.thresholds.anchorInvalidation.toFixed(3), '79.600');
});

test('pivot knownAt waits for both the right-side radius and the configured price turn', () => {
  const bars = goldenBars();
  const formation = firstGolden(analyze(bars, { minTurnPct: 30 }));
  const delayed = formation.support.touches.find(
    (touch) => touch.occurredAt === dateAt(60),
  );
  assert.ok(delayed);
  assert.equal(delayed.knownAt, dateAt(65));
  assert.notEqual(delayed.knownAt, dateAt(62));
});

test('a pivot confirmed after the first cross can never become frozen evidence later', () => {
  const bars = goldenBars(false);
  bars[60].low = 80;
  bars[83].low = 80;
  const formation = firstGolden(analyze(bars));
  assert.equal(formation.startedAt, dateAt(84));
  assert.equal(formation.support, null);
  append(bars, 105);
  append(bars, 110);
  assert.equal(firstGolden(analyze(bars)).support, null);
});

test('adjacent plateau lows are not separate support touches', () => {
  const bars = goldenBars(false);
  for (let index = 30; index <= 38; index++) bars[index].low = 80;
  assert.equal(firstGolden(analyze(bars)).support, null);
});

test('confirmed pivots still need an actual configured turn between two touches', () => {
  const bars = goldenBars(false);
  bars[60].low = 80;
  bars[70].low = 80;
  bars[83].high = 110;
  assert.equal(firstGolden(analyze(bars, { minTurnPct: 30 })).support, null);
});

test('zone clusters cannot chain nearby pivots into an ever-widening band', () => {
  const bars = goldenBars(false);
  for (const [index, price] of [
    [30, 80],
    [40, 81],
    [50, 82],
    [60, 83],
    [70, 84],
  ])
    bars[index].low = price;
  assert.equal(firstGolden(analyze(bars, { minTouches: 4 })).support, null);
});

test('support and resistance already effectively breached before freezing are not revived', () => {
  const supportInput = goldenBars();
  for (const index of [68, 69])
    Object.assign(supportInput[index], {
      open: 75,
      high: 75.2,
      low: 74.8,
      close: 75,
    });
  const support = firstGolden(analyze(supportInput));
  assert.equal(support.support, null);
  assert.match(support.zoneReasons.support, /已剔除有效破位/);
  const resistanceInput = goldenBars();
  for (const index of [66, 67])
    Object.assign(resistanceInput[index], {
      open: 145,
      high: 145.2,
      low: 144.8,
      close: 145,
    });
  const resistance = analyze(resistanceInput).formations.find(
    (row) => row.startedAt === dateAt(84),
  );
  assert.ok(resistance);
  assert.equal(resistance.resistance, null);
  assert.match(resistance.zoneReasons.resistance, /已剔除有效破位/);
});

test('direct support retest confirms independently before any resistance breakout', () => {
  const result = analyze(triangleFixtureBars, {}, dateAt(90));
  const formation = firstGolden(result);
  assert.equal(formation.state.kind, 'active');
  assert.equal(formation.state.retest.kind, 'confirmed');
  assert.equal(formation.state.breakout.kind, 'waiting');
  assert.deepEqual(
    formation.events.map((event) => event.kind),
    ['formed', 'support-touch', 'support-retest'],
  );
  assert.equal(formation.events[1].occurredAt, dateAt(89));
  assert.equal(formation.events[2].occurredAt, dateAt(89));
  assert.equal(formation.events[2].knownAt, dateAt(90));
  assert.equal(formation.events[2].price, 102);
});

test('breakout observation and confirmation preserve their separate dates and prices', () => {
  const formation = firstGolden(analyze(triangleFixtureBars));
  assert.equal(formation.state.breakout.kind, 'confirmed');
  assert.equal(formation.state.retest.kind, 'confirmed');
  const observed = formation.events.find(
    (event) => event.kind === 'resistance-breakout-observed',
  );
  const confirmed = formation.events.find(
    (event) => event.kind === 'resistance-breakout',
  );
  assert.equal(observed.knownAt, dateAt(91));
  assert.equal(observed.price, 134);
  assert.equal(confirmed.occurredAt, dateAt(91));
  assert.equal(confirmed.knownAt, dateAt(92));
  assert.equal(confirmed.price, 136);
  assert.ok(
    formation.events.every(
      (event) => event.kind === 'formed' || event.knownAt > formation.formedAt,
    ),
  );
});

test('a support touch remains visible even when its close does not satisfy the confirmation start', () => {
  const bars = goldenBars();
  for (const close of [200, 220, 240, 260, 280, 80.8]) append(bars, close);
  const formation = firstGolden(analyze(bars));
  assert.equal(formation.state.kind, 'active');
  assert.equal(formation.state.retest.kind, 'waiting');
  const touch = formation.events.find(
    (event) => event.kind === 'support-touch',
  );
  assert.ok(touch);
  assert.equal(touch.knownAt, bars.at(-1).date);
  assert.match(touch.reason, /守住条件未触发/);
  assert.ok(!formation.events.some((event) => event.kind === 'support-retest'));
});

test('already above resistance at formation is not a new post-formation breakout', () => {
  const bars = goldenBars().slice(0, 85);
  for (const close of [100, 115, 135, 140, 145]) append(bars, close);
  const formation = firstGolden(analyze(bars));
  assert.ok(formation.price > formation.resistance.upper * 1.005);
  assert.equal(formation.state.breakout.kind, 'waiting');
  assert.ok(
    !formation.events.some((event) =>
      event.kind.startsWith('resistance-breakout'),
    ),
  );
});

test('failed breakout tests debounce, retain observations and start new tests with stable IDs', () => {
  const bars = goldenBars();
  for (const close of [134, 130]) append(bars, close);
  const failed = firstGolden(analyze(bars));
  assert.equal(failed.state.breakout.kind, 'waiting');
  assert.equal(
    failed.events.filter(
      (event) => event.kind === 'resistance-breakout-observed',
    ).length,
    1,
  );
  assert.equal(
    failed.events.filter((event) => event.kind === 'resistance-breakout')
      .length,
    0,
  );
  for (const close of [135, 137]) append(bars, close);
  const confirmed = firstGolden(analyze(bars));
  assert.equal(
    confirmed.events.filter(
      (event) => event.kind === 'resistance-breakout-observed',
    ).length,
    2,
  );
  assert.equal(confirmed.state.breakout.occurredAt, dateAt(91));
  assert.equal(confirmed.state.breakout.knownAt, dateAt(92));
  assert.equal(confirmed.events[1].id, failed.events[1].id);
  assert.equal(
    new Set(confirmed.events.map((event) => event.id)).size,
    confirmed.events.length,
  );
});

test('failed support confirmation can restart without requiring a breakout', () => {
  const bars = goldenBars();
  append(bars, 100.5, 80.8);
  append(bars, 80.5);
  assert.equal(firstGolden(analyze(bars)).state.retest.kind, 'waiting');
  append(bars, 103, 80.8);
  append(bars, 105);
  const formation = firstGolden(analyze(bars));
  assert.equal(formation.state.retest.kind, 'confirmed');
  assert.equal(formation.state.retest.occurredAt, dateAt(91));
  assert.equal(formation.state.breakout.kind, 'waiting');
  assert.equal(
    formation.events.filter((event) => event.kind === 'support-touch').length,
    2,
  );
});

test('ambiguous candles that pierce support or the price anchor do not create positive observations', () => {
  const bars = goldenBars();
  append(bars, 134, 75);
  append(bars, 136);
  const formation = firstGolden(analyze(bars));
  assert.deepEqual(
    formation.events.map((event) => event.kind),
    ['formed'],
  );
  assert.equal(formation.state.breakout.kind, 'waiting');
  assert.equal(formation.state.retest.kind, 'waiting');
});

test('effective support and anchor losses use consecutive closes and retain prior successes', () => {
  for (const [zones, loss, reason] of [
    [true, 78, /冻结支撑/],
    [false, 85, /前置低点/],
  ]) {
    const bars = goldenBars(zones);
    for (const close of [200, 220, 240, 260, 280, loss]) append(bars, close);
    const oneLoss = firstGolden(analyze(bars));
    assert.equal(oneLoss.state.kind, 'active');
    append(bars, loss);
    const formation = firstGolden(analyze(bars));
    assert.equal(formation.state.kind, 'invalidated');
    assert.match(formation.state.reason, reason);
    assert.equal(formation.events.at(-1).occurredAt, dateAt(94));
    assert.equal(formation.events.at(-1).knownAt, dateAt(95));
    if (zones)
      assert.ok(
        formation.events.some((event) => event.kind === 'resistance-breakout'),
      );
  }
});

test('a recovered loss close resets the consecutive invalidation run', () => {
  const bars = goldenBars();
  for (const close of [200, 220, 240, 260, 280, 78, 300, 78])
    append(bars, close);
  assert.equal(firstGolden(analyze(bars)).state.kind, 'active');
});

test('reverse crossings and expiry take priority over same-day positive events', () => {
  const reversal = goldenBars();
  for (const close of [90, 90, 90, 90]) append(reversal, close, 80.8);
  const reversed = firstGolden(analyze(reversal));
  assert.equal(reversed.state.kind, 'invalidated');
  assert.match(reversed.state.reason, /反向交叉/);
  const invalidationDate = reversed.events.at(-1).knownAt;
  assert.ok(
    !reversed.events.some(
      (event) =>
        event.kind !== 'invalidated' && event.knownAt === invalidationDate,
    ),
  );
  const expiry = goldenBars();
  append(expiry, 100.5, 80.8);
  const expired = firstGolden(analyze(expiry, { expirySessions: 1 }));
  assert.equal(expired.state.kind, 'expired');
  assert.deepEqual(
    expired.events.map((event) => event.kind),
    ['formed', 'expired'],
  );
});

test('default lifecycle expires at 30 later sessions, with prior evidence and events preserved', () => {
  const bars = goldenBars(false);
  for (let index = 0; index < 29; index++) append(bars, 100.5 + index * 1.5);
  assert.equal(firstGolden(analyze(bars)).state.kind, 'active');
  append(bars, 144);
  const formation = firstGolden(analyze(bars));
  assert.equal(formation.state.kind, 'expired');
  assert.equal(formation.events.at(-1).knownAt, dateAt(118));
  assert.equal(formation.pretrend.from.date, dateAt(24));
});

test('cutoff input slicing ignores future prices, gaps and malformed future OHLC', () => {
  const prefix = triangleFixtureBars.slice(0, 91);
  const future = [
    ...triangleFixtureBars,
    { kind: 'gap', date: dateAt(93), reason: 'missing-day' },
    {
      kind: 'bar',
      date: dateAt(94),
      open: NaN,
      high: -1,
      low: 0,
      close: Infinity,
    },
  ];
  assert.deepEqual(
    analyze(future, {}, dateAt(90)),
    analyze(prefix, {}, dateAt(90)),
  );
});

test('appending future sessions cannot rewrite frozen evidence or any event already known', () => {
  const bars = structuredClone(triangleFixtureBars);
  for (const close of [150, 155, 160, 78, 78]) append(bars, close);
  bars.push({ kind: 'gap', date: dateAt(bars.length), reason: 'missing-day' });
  const full = analyze(bars);
  const final = firstGolden(full);
  for (let cutoff = 84; cutoff <= 92; cutoff++) {
    const earlier = analyze(bars.slice(0, cutoff + 1));
    assert.deepEqual(
      full.points.filter((point) => point.date <= dateAt(cutoff)),
      earlier.points,
    );
    assert.deepEqual(
      full.crosses.filter((cross) => cross.date <= dateAt(cutoff)),
      earlier.crosses,
    );
    if (cutoff < 88) continue;
    const prior = firstGolden(earlier);
    for (const field of [
      'id',
      'startedAt',
      'formedAt',
      'price',
      'crosses',
      'pretrend',
      'support',
      'resistance',
      'zoneReasons',
      'qualified',
      'reasons',
    ])
      assert.deepEqual(final[field], prior[field]);
    assert.deepEqual(
      final.events.filter((event) => event.knownAt <= dateAt(cutoff)),
      prior.events,
    );
  }
});

test('engine output is immutable and analysis never mutates caller bars or params', () => {
  const bars = goldenBars();
  const snapshot = structuredClone(bars);
  const params = { ...defaultTriangleParams };
  const result = analyzeTriangles({ bars, params, asOf: bars.at(-1).date });
  assert.deepEqual(bars, snapshot);
  assert.deepEqual(params, defaultTriangleParams);
  assert.equal(Object.isFrozen(bars), false);
  assert.ok(
    Object.isFrozen(result) &&
      Object.isFrozen(result.points[0].bar) &&
      Object.isFrozen(result.formations[0].events),
  );
  assert.throws(() => {
    result.formations[0].support.center = 1;
  }, TypeError);
});

test('boundary validation rejects invalid prices, bad OHLC, dates, order, duplicates and unsafe parameters', () => {
  const bars = goldenBars();
  assert.throws(
    () => analyze([bars[1], bars[0]], {}, '2026-11-04'),
    /严格递增/,
  );
  assert.throws(() => analyze([bars[0], bars[0]]), /不能重复/);
  assert.throws(() => analyze([{ ...bars[0], close: NaN }]), /有限正数/);
  assert.throws(
    () => analyze([{ ...bars[0], low: bars[0].close + 1 }]),
    /高低价关系/,
  );
  assert.throws(
    () => analyze([{ ...bars[0], date: '2026-02-30' }], {}, '2026-11-04'),
    /有效的/,
  );
  assert.throws(() => analyze(bars, {}, 'bad-date'), /观察截止日期/);
  assert.throws(
    () => validateTriangleParams({ ...defaultTriangleParams, fastPeriod: 10 }),
    /短均线 < 中均线/,
  );
  assert.throws(
    () =>
      validateTriangleParams({
        ...defaultTriangleParams,
        confirmSessions: 1.5,
      }),
    /整数/,
  );
  assert.throws(
    () =>
      validateTriangleParams({ ...defaultTriangleParams, minDeclinePct: NaN }),
    /有限数字/,
  );
  assert.throws(
    () =>
      validateTriangleParams({ ...defaultTriangleParams, expirySessions: 0 }),
    /有限数字/,
  );
  assert.throws(
    () =>
      validateTriangleParams({
        ...defaultTriangleParams,
        pivotRadius: 10,
        zoneLookbackSessions: 5,
      }),
    /左右窗口/,
  );
  assert.deepEqual(
    validateTriangleParams(defaultTriangleParams),
    defaultTriangleParams,
  );
  assert.equal(
    parameterFields.length,
    Object.keys(defaultTriangleParams).length,
  );
});
