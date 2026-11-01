import test from 'node:test';
import assert from 'node:assert/strict';
import {
  concentrationGroups,
  concentrationModel,
  concentrationRule,
  concentrationStreakText,
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
function source() {
  return {
    schema: 'stock.chips.market.manifest.v1',
    dataset_id: '0123456789abcdef',
    active_slot: '0123456789abcdef',
    as_of: days.at(-1),
    coefficient: 2,
    model_version: concentrationModel,
    universe_total: 3,
    scanned_count: 3,
    universe_coverage: 'SITE_CATALOG_UNVERIFIED',
  };
}
function document() {
  const groups = (state) =>
    Object.fromEntries(concentrationGroups.map((n) => [n, state]));
  return {
    schema: 'stock.chips.concentration.v1',
    rule_version: concentrationRule,
    dataset_id: source().dataset_id,
    active_slot: source().active_slot,
    as_of: source().as_of,
    coefficient: 2,
    model_version: concentrationModel,
    window_policy: 'EACH_DATE_INDEPENDENT_TRAILING_210_OBSERVED_BARS',
    sessions: days,
    universe_total: 3,
    processed_count: 3,
    source_universe_coverage: 'SITE_CATALOG_UNVERIFIED',
    groups: Object.fromEntries(
      concentrationGroups.map((n) => [n, { PASS: 1, FAIL: 1, UNKNOWN: 1 }]),
    ),
    rows: [
      {
        security_id: '999997',
        name: 'INVENTED_DECLINE',
        close_cny: '10',
        values: days.map((_, i) => 30 - i),
        point_reasons: {},
        short_history_days: 0,
        streak_days: 20,
        streak_boundary: 'WINDOW_LIMIT',
        groups: groups('PASS'),
      },
      {
        security_id: '999998',
        name: 'INVENTED_ZERO',
        close_cny: '10',
        values: days.map(() => 0),
        point_reasons: {},
        short_history_days: 0,
        streak_days: 0,
        streak_boundary: 'NON_DECREASE',
        groups: groups('FAIL'),
      },
      {
        security_id: '999999',
        name: 'INVENTED_MISSING',
        close_cny: null,
        values: days.map(() => null),
        point_reasons: Object.fromEntries(
          days.map((_, i) => [i, 'SOURCE_SESSION_MISSING']),
        ),
        short_history_days: 0,
        streak_days: 0,
        streak_boundary: 'UNAVAILABLE',
        groups: groups('UNKNOWN'),
      },
    ],
  };
}

test('complete dataset keeps zero, unavailable points and authoritative five-group decisions separate', () => {
  const result = parseConcentrationScan(
    document(),
    parseConcentrationSource(source()),
  );
  assert.equal(result.rows[1].values[20], 0);
  assert.equal(result.rows[2].values[20], null);
  assert.equal(result.groups[20].PASS, 1);
  assert.equal(result.source_universe_coverage, 'SITE_CATALOG_UNVERIFIED');
  assert.equal(concentrationStreakText(result.rows[0]), '≥20天');
  assert.equal(concentrationStreakText(result.rows[1]), '0天');
  assert.equal(concentrationStreakText(result.rows[2]), '待核实');
});

test('page source does not inherit unrelated price, limit-up or stability filters', () => {
  const result = parseConcentrationSource({
    ...source(),
    price_filter_rule: 'UNRELATED',
    chip_unchanged_rule: 'UNRELATED',
    screen_counts: { UNKNOWN: 3 },
  });
  assert.equal(result.universe_total, 3);
  assert.throws(() =>
    parseConcentrationSource({ ...source(), coefficient: 1 }),
  );
  assert.throws(() =>
    parseConcentrationSource({ ...source(), scanned_count: 2 }),
  );
});

test('parser refuses stale dataset, model, date and rule identities', () => {
  for (const [field, value] of [
    ['dataset_id', 'fedcba9876543210'],
    ['active_slot', 'fedcba9876543210'],
    ['as_of', '2026-10-29'],
    ['model_version', 'UNRELATED'],
    ['rule_version', 'UNRELATED'],
    ['coefficient', 1],
  ]) {
    assert.throws(() =>
      parseConcentrationScan(
        { ...document(), [field]: value },
        parseConcentrationSource(source()),
      ),
    );
  }
});

test('partial universe, duplicate stocks and mismatched group counts cannot become an empty valid result', () => {
  const partial = document();
  partial.processed_count = 2;
  assert.throws(() =>
    parseConcentrationScan(partial, parseConcentrationSource(source())),
  );
  const short = document();
  short.rows.pop();
  assert.throws(() =>
    parseConcentrationScan(short, parseConcentrationSource(source())),
  );
  const duplicate = document();
  duplicate.rows[1].security_id = duplicate.rows[0].security_id;
  assert.throws(() =>
    parseConcentrationScan(duplicate, parseConcentrationSource(source())),
  );
  const badCount = document();
  badCount.groups[3].PASS = 0;
  assert.throws(() =>
    parseConcentrationScan(badCount, parseConcentrationSource(source())),
  );
});

test('every missing point has a reason and every concentration is a finite number', () => {
  const missing = document();
  delete missing.rows[2].point_reasons['20'];
  assert.throws(() =>
    parseConcentrationScan(missing, parseConcentrationSource(source())),
  );
  for (const value of [NaN, Infinity, -1, '0']) {
    const invalid = document();
    invalid.rows[0].values[20] = value;
    assert.throws(() =>
      parseConcentrationScan(invalid, parseConcentrationSource(source())),
    );
  }
  const wrongReason = document();
  wrongReason.rows[0].point_reasons['20'] = 'SOURCE_SESSION_MISSING';
  assert.throws(() =>
    parseConcentrationScan(wrongReason, parseConcentrationSource(source())),
  );
});

test('21-date axis remains ordered and ends at the publication date', () => {
  const bad = document();
  bad.sessions = [...days];
  bad.sessions[5] = bad.sessions[4];
  assert.throws(() =>
    parseConcentrationScan(bad, parseConcentrationSource(source())),
  );
  const short = document();
  short.sessions = days.slice(1);
  assert.throws(() =>
    parseConcentrationScan(short, parseConcentrationSource(source())),
  );
  assert.throws(() =>
    parseConcentrationSource({ ...source(), as_of: '2026-02-30' }),
  );
});

test('concentration is a first-class authenticated site route with native unsupported paths preserved', () => {
  assert.ok(sitePaths.includes('/concentration'));
  assert.equal(sitePagePath('/concentration'), '/concentration');
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
