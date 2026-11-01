import test from 'node:test';
import assert from 'node:assert/strict';
import {
  concentrationGroups,
  concentrationModel,
  concentrationRule,
  concentrationStreakText,
  concentrationSeries,
  concentrationStartValue,
  concentrationDrop,
  parseConcentrationScan,
  parseConcentrationSource,
} from '../../src/lib/concentration-screen.ts';
import { sitePagePath, sitePaths } from '../../src/lib/site-routes.ts';

const days = [
  '2026-09-24',
  '2026-09-28',
  '2026-09-29',
  '2026-09-30',
  '2026-10-08',
  '2026-10-09',
  '2026-10-12',
  '2026-10-13',
  '2026-10-14',
  '2026-10-15',
  '2026-10-16',
  '2026-10-19',
  '2026-10-20',
  '2026-10-21',
  '2026-10-22',
  '2026-10-23',
  '2026-10-26',
  '2026-10-27',
  '2026-10-28',
  '2026-10-29',
  '2026-10-30',
];
const older = Array.from({ length: 85 }, (_, i) =>
  new Date(Date.UTC(2026, 6, 1 + i)).toISOString().slice(0, 10),
).filter((d) => d < days[0]);
const axis = [...older, ...days];
const groups = (state) =>
  Object.fromEntries(concentrationGroups.map((n) => [n, state]));
function source() {
  return {
    schema: 'stock.chips.market.manifest.v1',
    dataset_id: '0123456789abcdef',
    active_slot: '0123456789abcdef',
    as_of: days.at(-1),
    coefficient: 2,
    model_version: concentrationModel,
    universe_total: 4,
    scanned_count: 4,
    universe_coverage: 'SITE_CATALOG_UNVERIFIED',
  };
}
function recount(doc) {
  doc.groups = Object.fromEntries(
    concentrationGroups.map((n) => [
      n,
      Object.fromEntries(
        ['PASS', 'FAIL', 'UNKNOWN'].map((state) => [
          state,
          doc.rows.filter((r) => r.groups[n] === state).length,
        ]),
      ),
    ]),
  );
  return doc;
}
function document() {
  return recount({
    schema: 'stock.chips.concentration.two.v1',
    rule_version: concentrationRule,
    dataset_id: source().dataset_id,
    active_slot: source().active_slot,
    as_of: source().as_of,
    coefficient: 2,
    model_version: concentrationModel,
    window_policy: 'EACH_DATE_INDEPENDENT_TRAILING_210_OBSERVED_BARS',
    sessions: days,
    streak_sessions: axis,
    universe_total: 4,
    processed_count: 4,
    source_universe_coverage: source().universe_coverage,
    rows: [
      {
        security_id: '999996',
        name: 'INVENTED_TIES',
        close_cny: '10',
        values: Array(21).fill(20),
        point_reasons: {},
        short_history_days: 0,
        streak_days: 66,
        streak_boundary: {
          kind: 'INCREASE',
          previous_session: axis.at(-68),
          previous_value: 19,
          anchor_session: axis.at(-67),
          anchor_value: 20,
        },
        streak_start_session: axis.at(-67),
        streak_values: Array(67).fill(20),
        groups: groups('PASS'),
      },
      {
        security_id: '999997',
        name: 'INVENTED_INCREASE',
        close_cny: '10',
        values: [...Array(20).fill(0), 1],
        point_reasons: {},
        short_history_days: 0,
        streak_days: 0,
        streak_boundary: {
          kind: 'INCREASE',
          previous_session: days.at(-2),
          previous_value: 0,
          anchor_session: days.at(-1),
          anchor_value: 1,
        },
        streak_start_session: days.at(-1),
        groups: groups('FAIL'),
      },
      {
        security_id: '999998',
        name: 'INVENTED_MISSING',
        close_cny: null,
        values: Array(21).fill(null),
        point_reasons: Object.fromEntries(
          days.map((_, i) => [i, 'SOURCE_SESSION_MISSING']),
        ),
        short_history_days: 0,
        streak_days: 0,
        streak_boundary: {
          kind: 'UNAVAILABLE',
          session: days.at(-1),
          reason: 'SOURCE_SESSION_MISSING',
        },
        streak_start_session: null,
        groups: groups('UNKNOWN'),
      },
      {
        security_id: '999999',
        name: 'INVENTED_ZERO',
        close_cny: '10',
        values: Array(21).fill(0),
        point_reasons: {},
        short_history_days: 0,
        streak_days: 21,
        streak_boundary: {
          kind: 'UNAVAILABLE',
          session: axis.at(-23),
          reason: 'SOURCE_SESSION_MISSING',
        },
        streak_start_session: axis.at(-22),
        streak_values: Array(22).fill(0),
        groups: groups('PASS'),
      },
    ],
  });
}
const parse = (doc) =>
  parseConcentrationScan(doc, parseConcentrationSource(source()));

test('Two-group document has only two nonincreasing groups with actual counts, ties, zero and unknown', () => {
  const result = parse(document());
  assert.deepEqual(concentrationGroups, [20, 'LONG']);
  assert.equal(result.groups.LONG.PASS, 2);
  assert.equal(result.rows[3].values[20], 0);
  assert.equal(result.rows[2].values[20], null);
  assert.equal(concentrationStreakText(result.rows[0]), '66天');
  assert.equal(concentrationStreakText(result.rows[1]), '0天');
  assert.equal(concentrationStreakText(result.rows[2]), '待核实');
  assert.equal(concentrationStreakText(result.rows[3]), '≥21天');
});

test('LONG uses all N+1 points and its actual anchor while 20 days retains 21 points', () => {
  const scan = parse(document()),
    row = scan.rows[0];
  const long = concentrationSeries(scan, row, 'LONG');
  assert.equal(long.values.length, 67);
  assert.equal(long.sessions.length, 67);
  assert.equal(long.sessions[0], row.streak_start_session);
  assert.equal(long.highlightFrom, 0);
  assert.equal(concentrationSeries(scan, row, 20).values.length, 21);
  assert.equal(concentrationSeries(scan, row, 20).highlightFrom, 0);
  assert.equal(concentrationStartValue(row, 'LONG'), 20);
  assert.equal(concentrationDrop(row, 'LONG'), 0);
  assert.equal(concentrationStartValue(scan.rows[1], 'LONG'), null);
});

test('full precision accepts tiny decline and rejects a tiny increase despite equal display', () => {
  const doc = document(),
    row = doc.rows[0];
  row.streak_values = row.streak_values.map((_, i) => 20 - i * 0.000000001);
  row.values = row.streak_values.slice(-21);
  assert.equal(new Set(row.values.map((v) => v.toFixed(2))).size, 1);
  assert.equal(parse(doc).rows[0].groups.LONG, 'PASS');
  row.values[20] = row.values[19] + 0.0000000001;
  assert.throws(() => parse(doc));
});

test('LONG requires 22 readings and unavailable older NO_TRADE is unknown rather than failed', () => {
  const doc = document(),
    row = doc.rows[0];
  row.streak_values = Array(21).fill(20);
  row.streak_days = 20;
  row.streak_start_session = days[0];
  row.streak_boundary = {
    kind: 'UNAVAILABLE',
    session: axis.at(-22),
    reason: 'NO_TRADE',
  };
  row.groups.LONG = 'UNKNOWN';
  recount(doc);
  assert.equal(parse(doc).rows[0].groups.LONG, 'UNKNOWN');
  row.groups.LONG = 'PASS';
  recount(doc);
  assert.throws(() => parse(doc));
});

test('known no-trade after endpoint remains FAIL and latest null never displays continuous zero', () => {
  const doc = document(),
    row = doc.rows[1];
  row.values[20] = null;
  row.point_reasons['20'] = 'NO_TRADE';
  row.streak_start_session = null;
  row.streak_boundary = {
    kind: 'NO_TRADE',
    session: days[20],
    reason: 'NO_TRADE',
  };
  assert.equal(concentrationStreakText(parse(doc).rows[1]), '待核实');
});

test('page source keeps its universe independent of unrelated filters', () => {
  assert.equal(
    parseConcentrationSource({
      ...source(),
      price_filter_rule: 'UNRELATED',
      chip_unchanged_rule: 'UNRELATED',
    }).universe_total,
    4,
  );
  assert.throws(() =>
    parseConcentrationSource({ ...source(), coefficient: 1 }),
  );
  assert.throws(() =>
    parseConcentrationSource({ ...source(), scanned_count: 2 }),
  );
});

test('parser rejects strict V1 and stale dataset, rule, model, date and denominator', () => {
  for (const [field, value] of [
    ['schema', 'stock.chips.concentration.v1'],
    ['schema', 'stock.chips.concentration.v2'],
    ['rule_version', 'CENTRAL_90_STRICT_DECLINE_INDEPENDENT_210_V1'],
    ['dataset_id', 'fedcba9876543210'],
    ['active_slot', 'fedcba9876543210'],
    ['as_of', '2026-10-29'],
    ['model_version', 'UNRELATED'],
    ['coefficient', 1],
    ['processed_count', 3],
  ])
    assert.throws(() => parse({ ...document(), [field]: value }));
});

test('partial universe, duplicate stocks, invalid curves and mismatched counts fail atomically', () => {
  const mutations = [
    (d) => d.rows.pop(),
    (d) => (d.groups['3'] = {PASS:0, FAIL:4, UNKNOWN:0}),
    (d) => (d.rows[0].groups['5'] = 'PASS'),
    (d) => (d.rows[1].security_id = d.rows[0].security_id),
    (d) => (d.groups[20].PASS = 0),
    (d) => d.rows[0].streak_values.pop(),
    (d) => (d.rows[0].streak_start_session = days[0]),
    (d) => (d.rows[0].streak_boundary.previous_value = 21),
    (d) => (d.rows[0].streak_days = 200),
    (d) => (d.rows[0].streak_values[5] = 21),
    (d) => (d.rows[0].groups.LONG = 'UNKNOWN'),
  ];
  for (const mutate of mutations) {
    const doc = document();
    mutate(doc);
    assert.throws(() => parse(doc));
  }
});

test('missing points need reasons, finite values and an ordered shared session axis', () => {
  const missing = document();
  delete missing.rows[2].point_reasons['20'];
  assert.throws(() => parse(missing));
  for (const value of [NaN, Infinity, -1, 101, '0']) {
    const invalid = document();
    invalid.rows[0].values[20] = value;
    assert.throws(() => parse(invalid));
  }
  const reason = document();
  reason.rows[0].point_reasons['20'] = 'SOURCE_SESSION_MISSING';
  assert.throws(() => parse(reason));
  const duplicate = document();
  duplicate.streak_sessions[5] = duplicate.streak_sessions[4];
  assert.throws(() => parse(duplicate));
  const short = document();
  short.sessions = days.slice(1);
  assert.throws(() => parse(short));
  assert.throws(() =>
    parseConcentrationSource({ ...source(), as_of: '2026-02-30' }),
  );
});

test('concentration stays an authenticated site route and preserves existing routes', () => {
  assert.ok(sitePaths.includes('/concentration'));
  assert.equal(sitePagePath('/concentration/'), '/concentration');
  assert.equal(sitePagePath('/concentration/extra'), null);
  for (const route of [
    '/',
    '/chips',
    '/chips/calibration',
    '/trend',
    '/trend/research',
    '/golden',
  ])
    assert.equal(sitePagePath(route), route);
});
