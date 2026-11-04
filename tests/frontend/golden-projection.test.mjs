import test from 'node:test';
import assert from 'node:assert/strict';
import { PROJECTION_RULE, parseProjectionManifest, parseProjectionScreening, parseProjectionStatus, parseProjectionStock, projectionCounts, projectionList, projectionInitialSelection, projectionMatches } from '../../src/lib/golden-projection.ts';

const dataset = '0123456789abcdef', source = 'fedcba9876543210', asOf = '2026-11-02', policy = 'a'.repeat(64);
const nearLow = { code: '100001', name: '虚构近低', as_of: asOf, source_status: 'KNOWN', drawing_state: 'NEW_REFERENCE', category: 'NEAR_L', close: '8.12', H: '10.00', M: '9.00', L: '8.00', direction: 'DOWN', basis: 'CURRENT_REFERENCE', near_tags: ['NEAR_L'], intraday_tags: [], new_crossing: 'UNKNOWN', distance_percent: { H: '-18.8', M: '-9.777777', L: '1.5' }, reasons: ['仅用于软件验证'], structure_id: 'synthetic-structure' };
const nearAxis = { ...nearLow, code: '100002', name: '虚构近轴', category: 'NEAR_M', close: '9.02', near_tags: ['NEAR_M'], distance_percent: { H: '-9.8', M: '0.222222', L: '12.75' } };
const noDraw = { ...nearLow, code: '100003', name: '虚构不画', drawing_state: 'NO_DRAW', category: 'NO_DRAW', close: '7.80', H: null, M: null, L: null, direction: null, basis: 'NONE', near_tags: [], distance_percent: { H: null, M: null, L: null }, structure_id: null };
const unknown = { ...noDraw, code: '100004', name: '虚构缺失', source_status: 'UNKNOWN', drawing_state: 'UNKNOWN', category: 'UNKNOWN', close: null };
const history = { ...nearLow, code: '100005', name: '虚构历史', drawing_state: 'HISTORY', category: 'HISTORY', close: '7.50', near_tags: [], basis: 'NONE', distance_percent: { H: '-25', M: '-16.66667', L: '-6.25' } };
const rows = [nearLow, nearAxis, noDraw, unknown, history];
const counts = { ABOVE: 0, BELOW: 0, NEAR_M: 1, NEAR_L: 1, NEAR_H: 0, FAR: 0, AMBIGUOUS: 0, HISTORY: 1, NO_DRAW: 1, UNKNOWN: 1 };
function manifestValue() { return { schema: 'stock.golden-projection.daily.manifest.v1', dataset_id: dataset, source_dataset_id: source, as_of: asOf, generated_at: '2026-11-02T07:30:00Z', universe_total: 5, counts, state_counts: { CURRENT: 0, NEW_REFERENCE: 2, HISTORY: 1, NO_DRAW: 1, UNKNOWN: 1 }, rule_id: PROJECTION_RULE, policy_sha256: policy, scope: '虚构清单', limitations: ['仅验证软件'], source_generation_stale: false, source_universe_coverage: 'SYNTHETIC' }; }
function screeningValue() { return { schema: 'stock.golden-projection.daily.screen.v1', dataset_id: dataset, source_dataset_id: source, as_of: asOf, universe_total: 5, rule_id: PROJECTION_RULE, policy_sha256: policy, rows }; }
function bar(date, open, high, low, close) { return { date, open, high, low, close, volume_shares: '1000', amount_cny: '9000', turnover_percent: '0.1' }; }
function structureValue() { return { id: 'synthetic-structure', direction: 'DOWN', levels: { H: '10.00', M: '9.00', L: '8.00' }, origin: { date: '2026-10-20', field: 'high', price: '10.00', kind: 'H' }, axis: { date: '2026-10-22', field: 'high', price: '9.00' }, route: 'TURN', evidence_through: '2026-10-23', recorded_at: '2026-11-02T07:30:00Z', effective_from: '2026-11-03', life_state: 'OPEN', guides_end_on: null, reasons: ['完整虚构转折'], evidence: [{ date: '2026-10-22', field: 'high', value: '9.00', role: '中轴原价' }], role_zone: null }; }
function stockValue() { return { schema: 'stock.golden-projection.daily.stock.v1', dataset_id: dataset, source_dataset_id: source, as_of: asOf, code: nearLow.code, name: nearLow.name, rule_id: PROJECTION_RULE, policy_sha256: policy, row: nearLow, history: [bar('2026-10-20', '9.80', '10.00', '9.60', '9.70'), bar('2026-10-22', '8.80', '9.00', '8.70', '8.90'), bar('2026-10-23', '8.80', '8.90', '8.50', '8.60'), bar(asOf, '8.17', '8.20', '8.06', '8.12')], structure: structureValue(), candidate_next_session: structureValue(), rejected_candidates: [], search_trace: [], source: { provider: 'SYNTHETIC' } }; }
function searchStep() { return { direction: 'DOWN', origin: { date: '2026-10-20', field: 'high', price: '10.00' }, side: 'RIGHT', trigger: 'RIGHT_FIRST', outcome: 'SELECTED', candidates: [{ point: { date: '2026-10-22', field: 'high', price: '9.00' }, basis: 'TURN', qualification: 'CONFIRMED', evidence_through: '2026-10-23', result: 'OPEN', reason: '完整虚构次高' }] }; }
function parseStockForRule(value, ruleId = PROJECTION_RULE) {
  const manifest = parseProjectionManifest({ ...manifestValue(), rule_id: ruleId });
  const screen = parseProjectionScreening({ ...screeningValue(), rule_id: ruleId, rows: rows.map(row => row.code === value.row.code ? value.row : row) }, manifest);
  return parseProjectionStock({ ...value, rule_id: ruleId }, manifest, screen.rows.find(row => row.code === value.row.code));
}

test('five price categories are the default while abstentions, historical structures and unknowns remain separate', () => {
  const manifest = parseProjectionManifest(manifestValue()), screen = parseProjectionScreening(screeningValue(), manifest);
  assert.deepEqual(projectionCounts(screen.rows), counts);
  assert.deepEqual(projectionList(screen.rows).map(row => row.code), ['100002', '100001']);
  assert.equal(projectionMatches(noDraw, 'FEATURED'), false);
  assert.equal(projectionMatches(history, 'FEATURED'), false);
  assert.equal(projectionMatches(unknown, 'UNKNOWN'), true);
});
test('distance sorting, code/name search and selection operate on the filtered list', () => {
  assert.equal(projectionInitialSelection(rows), nearAxis.code);
  assert.equal(projectionInitialSelection(rows, noDraw.code), nearAxis.code);
  assert.equal(projectionInitialSelection(rows, noDraw.code, 'NO_DRAW'), noDraw.code);
  assert.equal(projectionInitialSelection(rows, '', 'ALL', '缺失'), unknown.code);
  assert.equal(projectionInitialSelection(rows, '', 'FEATURED', '不存在'), '');
  assert.deepEqual(projectionList(rows, 'ALL', '100001').map(row => row.code), [nearLow.code]);
});
test('strict wick filter removes only recovered boundary breaches from near-high and near-low classes', () => {
  const recovered = { ...nearLow, intraday_tags: ['LOWER_RECOVERED'] };
  const axisTouch = { ...nearAxis, intraday_tags: ['TOUCH_M'] };
  assert.equal(projectionMatches(recovered, 'NEAR_L', '', false), true);
  assert.equal(projectionMatches(recovered, 'NEAR_L', '', true), false);
  assert.equal(projectionMatches(axisTouch, 'NEAR_M', '', true), true);
  assert.equal(projectionMatches({ ...nearLow, intraday_tags: ['TOUCH_L'] }, 'NEAR_L', '', true), true);
});
test('manifest requires complete count partitions and a known rule identity', () => {
  assert.throws(() => parseProjectionManifest({ ...manifestValue(), counts: { ...counts, NO_DRAW: 0 } }));
  assert.throws(() => parseProjectionManifest({ ...manifestValue(), state_counts: { ...manifestValue().state_counts, NEW_REFERENCE: 3 } }));
  assert.throws(() => parseProjectionManifest({ ...manifestValue(), rule_id: 'OTHER' }));
  assert.throws(() => parseProjectionManifest({ ...manifestValue(), policy_sha256: 'not-a-hash' }));
});
const readableRules = ['GOLDEN_V21_HYBRID_DAILY_V1', 'GOLDEN_V21_HYBRID_DAILY_V2', 'GOLDEN_V21_HYBRID_DAILY_V3', 'GOLDEN_V21_HYBRID_DAILY_V4'];

test('the current method identity is V4', () => {
  assert.equal(PROJECTION_RULE, readableRules[3]);
});

for (const ruleId of readableRules) {
  test(`readable ${ruleId} keeps its identity through manifest, screening and stock detail`, () => {
    const manifest = parseProjectionManifest({ ...manifestValue(), rule_id: ruleId });
    const screening = parseProjectionScreening({ ...screeningValue(), rule_id: ruleId }, manifest);
    const stock = parseProjectionStock({ ...stockValue(), rule_id: ruleId }, manifest, screening.rows[0]);
    assert.equal(manifest.rule_id, ruleId);
    assert.equal(screening.rule_id, ruleId);
    assert.equal(stock.rule_id, ruleId);
  });
}

test('supported method versions cannot mix rule identities or policy hashes in a publication', () => {
  for (const ruleId of readableRules) {
    const otherRule = readableRules.find(value => value !== ruleId);
    const manifest = parseProjectionManifest({ ...manifestValue(), rule_id: ruleId });
    const screening = parseProjectionScreening({ ...screeningValue(), rule_id: ruleId }, manifest);
    assert.throws(() => parseProjectionScreening({ ...screeningValue(), rule_id: otherRule }, manifest));
    assert.throws(() => parseProjectionStock({ ...stockValue(), rule_id: otherRule }, manifest, screening.rows[0]));
    assert.throws(() => parseProjectionScreening({ ...screeningValue(), rule_id: ruleId, policy_sha256: 'b'.repeat(64) }, manifest));
    assert.throws(() => parseProjectionStock({ ...stockValue(), rule_id: ruleId, policy_sha256: 'b'.repeat(64) }, manifest, screening.rows[0]));
  }
  assert.throws(() => parseProjectionManifest({ ...manifestValue(), rule_id: 'GOLDEN_V21_HYBRID_DAILY_V5' }));
});

test('V3 and V4 require and validate search audit while V1 and V2 retain their original empty audit', () => {
  const value = stockValue(); delete value.search_trace;
  for (const rule of readableRules.slice(2)) {
    assert.throws(() => parseStockForRule(value, rule));
    const trace = [searchStep()];
    assert.deepEqual(parseStockForRule({ ...stockValue(), search_trace: trace }, rule).search_trace, trace);
    const wrongSide = { ...searchStep(), side: 'LEFT', trigger: 'RIGHT_NO_USABLE_COMBINATION' };
    assert.throws(() => parseStockForRule({ ...stockValue(), search_trace: [wrongSide] }, rule));
    const ended = { ...searchStep(), outcome: 'NO_USABLE_COMBINATION', candidates: [{ ...searchStep().candidates[0], result: 'REACHED' }] };
    assert.throws(() => parseStockForRule({ ...stockValue(), search_trace: [ended] }, rule));
  }
  for (const rule of readableRules.slice(0, 2)) assert.deepEqual(parseStockForRule(value, rule).search_trace, []);
});

test('V3 and V4 reject opposite-field axes without relabelling a legacy level', () => {
  const down = stockValue();
  down.history[1] = bar('2026-10-22', '9.05', '9.20', '9.00', '9.10');
  down.structure = { ...structureValue(), axis: { date: '2026-10-22', field: 'low', price: '9.00' }, evidence: [{ date: '2026-10-22', field: 'low', value: '9.00', role: '旧支撑来源' }] };
  down.candidate_next_session = null;
  assert.equal(parseStockForRule(down, readableRules[1]).structure.axis.field, 'low');
  for (const rule of readableRules.slice(2)) assert.throws(() => parseStockForRule(down, rule));
  const up = stockValue();
  up.row = { ...nearLow, direction: 'UP' };
  up.history[0] = bar('2026-10-20', '8.20', '8.50', '8.00', '8.30');
  up.structure = { ...structureValue(), direction: 'UP', origin: { date: '2026-10-20', field: 'low', price: '8.00', kind: 'L' } };
  up.candidate_next_session = null;
  assert.equal(parseStockForRule(up, readableRules[1]).structure.axis.field, 'high');
  for (const rule of readableRules.slice(2)) assert.throws(() => parseStockForRule(up, rule));
});

test('search audit binds actual source fields, side, chronology and known outcomes', () => {
  const value = { ...stockValue(), search_trace: [searchStep()] };
  const parsed = parseStockForRule(value);
  assert.equal(parsed.search_trace[0].side, 'RIGHT');
  assert.equal(parsed.search_trace[0].candidates[0].point.field, 'high');
  const change = step => ({ ...value, search_trace: [step] });
  assert.throws(() => parseStockForRule(change({ ...searchStep(), side: 'LEFT', trigger: 'RIGHT_NO_USABLE_COMBINATION' })));
  assert.throws(() => parseStockForRule(change({ ...searchStep(), trigger: 'LEFT_FIRST' })));
  assert.throws(() => parseStockForRule(change({ ...searchStep(), candidates: [{ ...searchStep().candidates[0], point: { ...searchStep().candidates[0].point, price: '8.99' } }] })));
  assert.throws(() => parseStockForRule(change({ ...searchStep(), candidates: [{ ...searchStep().candidates[0], point: { date: '2026-10-22', field: 'low', price: '8.70' } }] })));
  assert.throws(() => parseStockForRule(change({ ...searchStep(), candidates: [{ ...searchStep().candidates[0], evidence_through: '2026-11-03' }] })));
  assert.throws(() => parseStockForRule(change({ ...searchStep(), candidates: [{ ...searchStep().candidates[0], evidence_through: null }] })));
  assert.throws(() => parseStockForRule(change({ ...searchStep(), candidates: [{ ...searchStep().candidates[0], result: 'BETTER_TARGET' }] })));
});

test('search audit keeps early rejection distinct from a formed combination reaching its target', () => {
  const first = searchStep();
  first.outcome = 'NO_USABLE_COMBINATION';
  first.candidates[0] = { ...first.candidates[0], result: 'TARGET_BEFORE_EVIDENCE', reason: '证据齐备前已到推算端' };
  const left = { ...searchStep(), side: 'LEFT', trigger: 'RIGHT_NO_USABLE_COMBINATION', candidates: [{ ...searchStep().candidates[0], point: { date: '2026-10-19', field: 'high', price: '9.50' }, evidence_through: '2026-10-20' }] };
  const value = { ...stockValue(), history: [bar('2026-10-19', '9.35', '9.50', '9.20', '9.40'), ...stockValue().history], search_trace: [first, left] };
  const parsed = parseStockForRule(value);
  assert.equal(parsed.search_trace[0].candidates[0].result, 'TARGET_BEFORE_EVIDENCE');
  assert.equal(parsed.search_trace[1].side, 'LEFT');
  const reached = { ...searchStep(), candidates: [{ ...searchStep().candidates[0], result: 'REACHED', reason: '成立后首次到价，保留历史' }] };
  const historyResult = parseStockForRule({ ...stockValue(), search_trace: [reached] });
  assert.equal(historyResult.search_trace.length, 1);
  assert.equal(historyResult.search_trace[0].candidates[0].result, 'REACHED');
});

test('pending qualifications remain inspectable even when no drawing is selected', () => {
  const value = stockValue();
  value.code = noDraw.code; value.name = noDraw.name; value.row = noDraw;
  value.history[value.history.length - 1] = bar(asOf, '7.90', '8.00', '7.70', '7.80');
  value.structure = null; value.candidate_next_session = null;
  value.search_trace = [{ ...searchStep(), outcome: 'QUALIFICATION_PENDING', candidates: [{ ...searchStep().candidates[0], basis: 'PLATFORM', qualification: 'PENDING', evidence_through: null, result: 'UNASSESSED', reason: '只有平台事实，阶段意义待核' }] }];
  const parsed = parseStockForRule(value);
  assert.equal(parsed.structure, null);
  assert.equal(parsed.search_trace[0].candidates[0].qualification, 'PENDING');
  assert.equal(parsed.search_trace[0].candidates[0].result, 'UNASSESSED');
});

test('a confirmed historical secondary remains confirmed while its current pairing is pending', () => {
  const step = { ...searchStep(), side: 'LEFT', trigger: 'RIGHT_NO_USABLE_COMBINATION', outcome: 'QUALIFICATION_PENDING', candidates: [{ ...searchStep().candidates[0], point: { date: '2026-10-16', field: 'high', price: '9.50' }, evidence_through: '2026-10-19', result: 'PAIR_EVIDENCE_PENDING', reason: '历史结构已确认，本轮配对证据仍待核' }] };
  const right = { ...searchStep(), outcome: 'NO_USABLE_COMBINATION', candidates: [] };
  const value = { ...stockValue(), history: [bar('2026-10-16', '9.35', '9.50', '9.20', '9.40'), ...stockValue().history], search_trace: [right, step] };
  const candidate = parseStockForRule(value).search_trace[1].candidates[0];
  assert.equal(candidate.qualification, 'CONFIRMED');
  assert.equal(candidate.basis, 'TURN');
  assert.equal(candidate.result, 'PAIR_EVIDENCE_PENDING');
  assert.equal(candidate.evidence_through, '2026-10-19');
});

test('search qualifications reject contradictory basis, evidence and result combinations', () => {
  const candidate = searchStep().candidates[0];
  const cases = [
    { ...candidate, basis: 'PLATFORM' },
    { ...candidate, result: 'UNASSESSED' },
    { ...candidate, basis: 'PLATFORM', qualification: 'PENDING', result: 'UNASSESSED' },
    { ...candidate, basis: 'PLATFORM', qualification: 'PENDING', evidence_through: null },
  ];
  for (const value of cases) {
    assert.throws(() => parseStockForRule({ ...stockValue(), search_trace: [{ ...searchStep(), outcome: 'QUALIFICATION_PENDING', candidates: [value] }] }), JSON.stringify(value));
  }
});

test('unconfirmed turn and role candidates retain their actual evidence kind', () => {
  for (const basis of ['TURN', 'ROLE']) {
    const step = { ...searchStep(), outcome: 'QUALIFICATION_PENDING', candidates: [{ ...searchStep().candidates[0], basis, qualification: 'PENDING', evidence_through: null, result: 'UNASSESSED', reason: '候选尚未取得完整确认' }] };
    const candidate = parseStockForRule({ ...stockValue(), search_trace: [step] }).search_trace[0].candidates[0];
    assert.equal(candidate.basis, basis);
    assert.equal(candidate.qualification, 'PENDING');
    assert.equal(candidate.evidence_through, null);
    assert.equal(candidate.result, 'UNASSESSED');
  }
});

test('search origins and candidate points must exist in the supplied history', () => {
  const step = searchStep();
  assert.throws(() => parseStockForRule({ ...stockValue(), search_trace: [{ ...step, origin: { ...step.origin, date: '2026-10-19' } }] }));
  assert.throws(() => parseStockForRule({ ...stockValue(), search_trace: [{ ...step, candidates: [{ ...step.candidates[0], point: { ...step.candidates[0].point, date: '2026-10-21' } }] }] }));
});

test('left search requires an earlier right search for the same fixed origin', () => {
  const left = { ...searchStep(), side: 'LEFT', trigger: 'RIGHT_NO_USABLE_COMBINATION', candidates: [{ ...searchStep().candidates[0], point: { date: '2026-10-19', field: 'high', price: '9.50' }, evidence_through: '2026-10-20' }] };
  const value = { ...stockValue(), history: [bar('2026-10-19', '9.35', '9.50', '9.20', '9.40'), ...stockValue().history] };
  const otherRight = { ...searchStep(), origin: { date: '2026-10-23', field: 'high', price: '8.90' }, outcome: 'NO_USABLE_COMBINATION', candidates: [{ ...searchStep().candidates[0], point: { date: asOf, field: 'high', price: '8.20' }, evidence_through: asOf, result: 'TARGET_BEFORE_EVIDENCE' }] };
  assert.throws(() => parseStockForRule({ ...value, search_trace: [left] }));
  assert.throws(() => parseStockForRule({ ...value, search_trace: [otherRight, left] }));
});

test('formed right results cannot be declared unusable or pending', () => {
  for (const result of ['OPEN', 'REACHED', 'INVALID']) {
    for (const outcome of ['NO_USABLE_COMBINATION', 'QUALIFICATION_PENDING']) {
      const step = { ...searchStep(), outcome, candidates: [{ ...searchStep().candidates[0], result }] };
      assert.throws(() => parseStockForRule({ ...stockValue(), search_trace: [step] }), `${result}:${outcome}`);
    }
  }
});

test('selected or ambiguous right searches cannot continue on the left', () => {
  const left = { ...searchStep(), side: 'LEFT', trigger: 'RIGHT_NO_USABLE_COMBINATION', candidates: [{ ...searchStep().candidates[0], point: { date: '2026-10-19', field: 'high', price: '9.50' }, evidence_through: '2026-10-20' }] };
  const value = { ...stockValue(), history: [bar('2026-10-19', '9.35', '9.50', '9.20', '9.40'), ...stockValue().history] };
  for (const outcome of ['SELECTED', 'AMBIGUOUS']) {
    assert.throws(() => parseStockForRule({ ...value, search_trace: [{ ...searchStep(), outcome }, left] }));
  }
});

test('a selected search step must contain a formed combination', () => {
  const step = searchStep();
  for (const candidates of [[], [{ ...step.candidates[0], result: 'TARGET_BEFORE_EVIDENCE' }], [{ ...step.candidates[0], result: 'PAIR_EVIDENCE_PENDING' }], [{ ...step.candidates[0], basis: 'PLATFORM', qualification: 'PENDING', evidence_through: null, result: 'UNASSESSED' }]]) {
    assert.throws(() => parseStockForRule({ ...stockValue(), search_trace: [{ ...step, candidates }] }));
  }
});

test('pending right pairing allows left inspection without upgrading platform evidence', () => {
  const right = { ...searchStep(), outcome: 'QUALIFICATION_PENDING', candidates: [{ ...searchStep().candidates[0], result: 'PAIR_EVIDENCE_PENDING', reason: '本轮配对证据待核' }] };
  const left = { ...searchStep(), side: 'LEFT', trigger: 'RIGHT_NO_USABLE_COMBINATION', outcome: 'QUALIFICATION_PENDING', candidates: [{ ...searchStep().candidates[0], point: { date: '2026-10-19', field: 'high', price: '9.50' }, basis: 'PLATFORM', qualification: 'PENDING', evidence_through: null, result: 'UNASSESSED', reason: '左侧平台资格待核' }] };
  const value = { ...stockValue(), history: [bar('2026-10-19', '9.35', '9.50', '9.20', '9.40'), ...stockValue().history], search_trace: [right, left] };
  const parsed = parseStockForRule(value);
  assert.equal(parsed.search_trace[0].candidates[0].qualification, 'CONFIRMED');
  assert.equal(parsed.search_trace[1].candidates[0].qualification, 'PENDING');
  assert.equal(parsed.search_trace[1].candidates[0].result, 'UNASSESSED');
});

test('one fixed origin cannot restart a completed search with different price formatting', () => {
  const restart = { ...searchStep(), origin: { ...searchStep().origin, price: '10.0' }, outcome: 'NO_USABLE_COMBINATION', candidates: [] };
  assert.throws(() => parseStockForRule({ ...stockValue(), search_trace: [searchStep(), restart] }));
});

test('screening cannot mix dates, policies, source batches or duplicate stocks', () => {
  const manifest = parseProjectionManifest(manifestValue());
  assert.throws(() => parseProjectionScreening({ ...screeningValue(), as_of: '2026-10-30' }, manifest));
  assert.throws(() => parseProjectionScreening({ ...screeningValue(), policy_sha256: 'b'.repeat(64) }, manifest));
  assert.throws(() => parseProjectionScreening({ ...screeningValue(), source_dataset_id: 'f'.repeat(16) }, manifest));
  assert.throws(() => parseProjectionScreening({ ...screeningValue(), rows: [nearLow, nearLow, noDraw, unknown, history] }, manifest));
});
test('new references cannot claim a new crossing and unknown sources cannot appear as current drawings', () => {
  const manifest = parseProjectionManifest(manifestValue());
  assert.throws(() => parseProjectionScreening({ ...screeningValue(), rows: [{ ...nearLow, new_crossing: 'DOWN' }, ...rows.slice(1)] }, manifest));
  assert.throws(() => parseProjectionScreening({ ...screeningValue(), rows: [{ ...nearLow, source_status: 'UNKNOWN' }, ...rows.slice(1)] }, manifest));
  assert.throws(() => parseProjectionScreening({ ...screeningValue(), rows: [{ ...nearLow, H: '8' }, ...rows.slice(1)] }, manifest));
});
test('stock detail binds the precise list row and includes actual current OHLC', () => {
  const manifest = parseProjectionManifest(manifestValue()), screen = parseProjectionScreening(screeningValue(), manifest), expected = screen.rows[0];
  const stock = parseProjectionStock(stockValue(), manifest, expected);
  assert.equal(stock.history.at(-1).date, asOf);
  assert.equal(stock.structure.axis.price, '9.00');
  assert.throws(() => parseProjectionStock({ ...stockValue(), row: { ...nearLow, close: '8.13' } }, manifest, expected));
  assert.throws(() => parseProjectionStock({ ...stockValue(), policy_sha256: 'b'.repeat(64) }, manifest, expected));
  assert.throws(() => parseProjectionStock({ ...stockValue(), source_dataset_id: 'f'.repeat(16) }, manifest, expected));
});
test('stock rejects future candles, missing current candles and fabricated source-point fields', () => {
  const manifest = parseProjectionManifest(manifestValue()), expected = parseProjectionScreening(screeningValue(), manifest).rows[0];
  const value = stockValue();
  assert.throws(() => parseProjectionStock({ ...value, history: [...value.history, bar('2026-11-03', '8.1', '8.2', '8', '8.1')] }, manifest, expected));
  assert.throws(() => parseProjectionStock({ ...value, history: value.history.slice(0, -1) }, manifest, expected));
  assert.throws(() => parseProjectionStock({ ...value, structure: { ...structureValue(), axis: { ...structureValue().axis, price: '8.99' } } }, manifest, expected));
  assert.throws(() => parseProjectionStock({ ...value, structure: { ...structureValue(), evidence: [{ date: '2026-10-22', field: 'low', value: '9.00', role: '伪造低点' }] } }, manifest, expected));
});
test('history guides cannot extend beyond the current day or stop before confirmation evidence', () => {
  const manifest = parseProjectionManifest(manifestValue()), expected = parseProjectionScreening(screeningValue(), manifest).rows[0];
  const value = stockValue();
  assert.throws(() => parseProjectionStock({ ...value, structure: { ...structureValue(), guides_end_on: '2026-11-03' } }, manifest, expected));
  assert.throws(() => parseProjectionStock({ ...value, structure: { ...structureValue(), guides_end_on: '2026-10-20' } }, manifest, expected));
});
test('status exposes a new target without relabelling the previous committed publication', () => {
  const status = parseProjectionStatus({ schema: 'stock.golden-projection.daily.status.v1', state: 'SOURCE_PENDING', as_of: '2026-11-03', source_dataset_id: null, dataset_id: null, universe_total: 5, scanned_count: 0, updated_at: '2026-11-03T08:00:00Z', message: '等待完整来源', last_committed_date: asOf });
  assert.equal(status.last_committed_date, asOf);
  assert.equal(parseProjectionManifest(manifestValue()).as_of, asOf);
  assert.throws(() => parseProjectionStatus({ ...status, scanned_count: 6 }));
});

const indexSource = '1122334455667788';
const shanghai = { ...nearAxis, code: 'sh000001', name: '上证指数' };
const growth = { ...unknown, code: 'sz399006', name: '创业板指数' };
function indexedManifestValue() {
  return { ...manifestValue(), index_source_dataset_id: indexSource, index_symbols: ['sh000001', 'sz399006'],
    stock_total: 5, index_total: 2, universe_total: 7,
    counts: { ...counts, NEAR_M: 2, UNKNOWN: 2 },
    state_counts: { ...manifestValue().state_counts, NEW_REFERENCE: 3, UNKNOWN: 2 } };
}
function indexedScreeningValue() {
  return { ...screeningValue(), index_source_dataset_id: indexSource, universe_total: 7, rows: [...rows, shanghai, growth] };
}
function indexStockValue() {
  const value = stockValue();
  return { ...value, source_dataset_id: indexSource, code: shanghai.code, name: shanghai.name, row: shanghai,
    history: [...value.history.slice(0, -1), bar(asOf, '9.01', '9.20', '8.90', '9.02')],
    source: { provider: 'SYNTHETIC_INDEX', price_unit: 'INDEX_POINTS', volume_unit: 'SHARES',
      key: `data/golden-projection/daily/index-snapshots/${indexSource}/sh000001.json` } };
}

test('legacy publications remain stock-only and index metadata binds exact identities and consistent totals', () => {
  const legacy = parseProjectionManifest(manifestValue());
  assert.equal(legacy.index_source_dataset_id, null);
  assert.deepEqual(legacy.index_symbols, []);
  assert.equal(legacy.stock_total, 5);
  assert.equal(legacy.index_total, 0);
  const manifest = parseProjectionManifest(indexedManifestValue());
  assert.equal(manifest.index_source_dataset_id, indexSource);
  assert.equal(manifest.stock_total, 5);
  assert.equal(manifest.index_total, 2);
  assert.throws(() => parseProjectionManifest({ ...indexedManifestValue(), index_symbols: ['000001', 'sz399006'] }));
  assert.throws(() => parseProjectionManifest({ ...indexedManifestValue(), index_symbols: ['sh000001', 'sh000001'] }));
  assert.throws(() => parseProjectionManifest({ ...indexedManifestValue(), index_source_dataset_id: null }));
  assert.throws(() => parseProjectionManifest({ ...indexedManifestValue(), stock_total: 7 }));
  assert.throws(() => parseProjectionManifest({ ...indexedManifestValue(), index_total: 1 }));
});

test('screening includes both declared indices without confusing the Shanghai index and bank', () => {
  const manifest = parseProjectionManifest(indexedManifestValue());
  const screen = parseProjectionScreening(indexedScreeningValue(), manifest);
  assert.equal(screen.rows[5].code, 'sh000001');
  assert.equal(screen.rows[6].code, 'sz399006');
  assert.equal(screen.rows[6].category, 'UNKNOWN');
  assert.throws(() => parseProjectionScreening({ ...indexedScreeningValue(), index_source_dataset_id: source }, manifest));
  assert.throws(() => parseProjectionScreening({ ...indexedScreeningValue(), rows: [...rows, { ...shanghai, code: '000001' }, growth] }, manifest));
  assert.throws(() => parseProjectionScreening({ ...indexedScreeningValue(), rows: [...rows, { ...shanghai, code: 'sh000002' }, growth] }, manifest));
  assert.throws(() => parseProjectionScreening({ ...indexedScreeningValue(), rows: [...rows, { ...shanghai, name: '平安银行' }, growth] }, manifest));
  const legacy = parseProjectionManifest(manifestValue());
  assert.throws(() => parseProjectionScreening({ ...screeningValue(), rows: [shanghai, ...rows.slice(1)] }, legacy));
});

test('known index details bind their own source batch, point units and actual index source path', () => {
  const manifest = parseProjectionManifest(indexedManifestValue());
  const screening = parseProjectionScreening(indexedScreeningValue(), manifest), expected = screening.rows[5];
  const value = indexStockValue();
  assert.equal(parseProjectionStock(value, manifest, expected).source_dataset_id, indexSource);
  assert.equal(parseProjectionStock(stockValue(), manifest, screening.rows[0]).source_dataset_id, source);
  assert.throws(() => parseProjectionStock({ ...stockValue(), source_dataset_id: indexSource }, manifest, screening.rows[0]));
  assert.throws(() => parseProjectionStock({ ...value, source_dataset_id: source }, manifest, expected));
  assert.throws(() => parseProjectionStock({ ...value, source: { ...value.source, price_unit: 'CNY' } }, manifest, expected));
  assert.throws(() => parseProjectionStock({ ...value, source: { ...value.source, volume_unit: 'HAND' } }, manifest, expected));
  assert.throws(() => parseProjectionStock({ ...value, source: { provider: 'SYNTHETIC_INDEX' } }, manifest, expected));
  assert.throws(() => parseProjectionStock({ ...value, source: { ...value.source, key: `data/chips/daily/${source}/000001.json` } }, manifest, expected));
  assert.throws(() => parseProjectionStock({ ...value, source: { ...value.source, key: `data/golden-projection/daily/index-snapshots/${source}/sh000001.json` } }, manifest, expected));
});

test('unavailable index data remains UNKNOWN without manufacturing a source or candles', () => {
  const manifest = parseProjectionManifest(indexedManifestValue());
  const expected = parseProjectionScreening(indexedScreeningValue(), manifest).rows[6];
  const value = { ...indexStockValue(), code: growth.code, name: growth.name, row: growth,
    history: [], structure: null, candidate_next_session: null, source: { key: null, provider: 'UNKNOWN' } };
  const parsed = parseProjectionStock(value, manifest, expected);
  assert.equal(parsed.row.category, 'UNKNOWN');
  assert.deepEqual(parsed.history, []);
  assert.equal(parsed.structure, null);
});
