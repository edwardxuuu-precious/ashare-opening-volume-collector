import test from 'node:test';
import assert from 'node:assert/strict';
import {
  parseCycleSnapshot,
  parseCycleStatus,
} from '../../src/lib/cycle-bottom-data.ts';

function inventedSnapshot() {
  const points = Array.from({ length: 500 }, (_, index) => {
    const equity = 100 + index,
      reference = 100 + index / 2,
      fitted = 95 + index;
    return {
      date: new Date(Date.UTC(2010, 0, index + 1)).toISOString().slice(0, 10),
      equity,
      reference,
      fitted,
      lower: fitted - 5,
      upper: fitted + 5,
      gapPct: (equity / fitted - 1) * 100,
      rawGapPct: (equity / reference - 1) * 100,
    };
  });
  const source = (id, code) => ({
    id,
    code,
    name: 'ALL VALUES INVENTED',
    provider: 'INVENTED',
    firstDate: points[0].date,
    lastDate: points.at(-1).date,
    rows: 500,
    excludedRows: 0,
    missingSessions: 0,
    url: 'https://invented.invalid',
    note: 'fixture',
  });
  return {
    schema: 'stock.cycle-bottom.v1',
    version: 'a'.repeat(16),
    generatedAt: '2026-11-08T00:00:00Z',
    expectedAsOf: points.at(-1).date,
    sources: [
      source('equity', 'H00300'),
      source('government', '000012'),
      source('shanghai', '000001'),
    ],
    comparisons: [
      {
        id: 'government',
        name: 'INVENTED',
        code: '000012',
        asOf: points.at(-1).date,
        baseDate: points[0].date,
        points,
        fit: {
          method: 'INVENTED',
          trainStart: '2010-01-01',
          trainEnd: '2019-12-31',
          quantile: 0.1,
          alpha: 1,
          beta: 2,
          samples: 500,
          trainingMinGapPct: -10,
          holdoutMinGapPct: -20,
          holdoutBelowTrainingDays: 20,
        },
        latest: {
          equityClose: 100,
          referenceClose: 100,
          fittedClose: 95,
          gapPct: points.at(-1).gapPct,
          rawGapPct: points.at(-1).rawGapPct,
          gapChange20Pp: null,
        },
        cases: [],
        events: [],
        rawTouchEvents: [],
        thresholdPct: 3,
        cooldownSessions: 63,
      },
    ],
    monthly: {
      asOf: points.at(-1).date,
      points: [
        {
          date: '2011-05-15',
          month: '2011-05',
          open: 100,
          high: 120,
          low: 90,
          close: 110,
          lower: 80,
          middle: 100,
          upper: 130,
          incomplete: false,
        },
      ],
      fit: {
        method: 'INVENTED',
        trainStart: '1997-07-01',
        trainEnd: '2019-12-31',
        samples: 270,
        annualLogSlope: 0.04,
      },
      latestGapPct: 10,
    },
    observations: [
      { title: 'INVENTED', detail: 'No real investment conclusion.' },
    ],
    unavailable: [
      { code: '885007.WI', name: 'INVENTED', reason: 'Authorization pending.' },
    ],
  };
}

test('valid invented snapshot preserves available numbers and unavailable source state', () => {
  const result = parseCycleSnapshot(inventedSnapshot());
  assert.equal(result.comparisons[0].points.length, 500);
  assert.equal(result.comparisons[0].latest.gapChange20Pp, null);
  assert.equal(result.unavailable[0].code, '885007.WI');
});
test('price-only CSI300 cannot masquerade as the total-return source', () => {
  const value = inventedSnapshot();
  value.sources[0].code = '000300';
  assert.throws(() => parseCycleSnapshot(value), /身份/);
});
test('displayed distance must agree with the actual same-date lines', () => {
  const value = inventedSnapshot();
  value.comparisons[0].points[50].gapPct = -50;
  assert.throws(() => parseCycleSnapshot(value), /差距/);
});
test('duplicate or unsorted dates are rejected rather than silently relabeled', () => {
  const value = inventedSnapshot();
  value.comparisons[0].points[20].date = value.comparisons[0].points[19].date;
  assert.throws(() => parseCycleSnapshot(value), /递增/);
});
test('unmatured forward windows never become zero-return successes', () => {
  const value = inventedSnapshot();
  value.comparisons[0].events.push({
    date: '2011-05-15',
    gapPct: 2,
    phase: 'holdout',
    outcomes: [
      { sessions: 252, complete: false, returnPct: 0, downsidePct: 0 },
    ],
  });
  assert.throws(() => parseCycleSnapshot(value), /未成熟/);
});
test('invalid monthly OHLC is rejected before rendering a candle', () => {
  const value = inventedSnapshot();
  value.monthly.points[0].high = 95;
  assert.throws(() => parseCycleSnapshot(value), /月线/);
});
test('failed update status preserves the last-success date and failure message', () => {
  const result = parseCycleStatus({
    state: 'failed',
    checkedAt: '2026-11-08T02:00:00Z',
    lastSuccessAt: '2026-11-06T10:00:00Z',
    message: 'INVENTED FAILURE',
    schedule: 'INVENTED SCHEDULE',
  });
  assert.equal(result.state, 'failed');
  assert.equal(result.lastSuccessAt, '2026-11-06T10:00:00Z');
});
