import { createHash } from 'node:crypto';
import {
  analyzeTriangles,
  defaultTriangleParams,
} from '../../../src/lib/triangles.ts';
import { triangleDailyRuleSha256 } from '../../../src/lib/triangle-publication.ts';
import { triangleFixtureBars } from './triangles.mjs';

export const jsonBytes = (value) => Buffer.from(JSON.stringify(value));
export const fileVersion = (file, value) => ({
  file,
  bytes: jsonBytes(value).length,
  sha256: createHash('sha256').update(jsonBytes(value)).digest('hex'),
});

function dates(length) {
  const result = [];
  const current = new Date('2026-01-02T00:00:00Z');
  while (result.length < length) {
    if (current.getUTCDay() !== 0 && current.getUTCDay() !== 6)
      result.push(current.toISOString().slice(0, 10));
    current.setUTCDate(current.getUTCDate() + 1);
  }
  return result;
}

export function trianglePublicationFixture(dataset = 'abcdef0123456789') {
  const sessions = dates(180);
  const golden = [
    ...Array.from({ length: 87 }, () => triangleFixtureBars[0]),
    ...triangleFixtureBars,
  ].map((bar, index) => ({ ...bar, date: sessions[index] }));
  const death = golden.map((bar) => ({
    kind: 'bar',
    date: bar.date,
    open: 200 - bar.open,
    high: 200 - bar.low,
    low: 200 - bar.high,
    close: 200 - bar.close,
  }));
  const cycleCloses = [
    ...Array.from({ length: 80 }, (_, index) => 120 - index * 0.7),
    ...Array.from({ length: 12 }, (_, index) => 65 + index * 1.6),
    ...Array.from({ length: 78 }, (_, index) => 84 + index * 1.2),
    ...Array.from({ length: 10 }, (_, index) => 176 - index * 2.8),
  ];
  const cycle = cycleCloses.map((close, index) => ({
    kind: 'bar',
    date: sessions[index],
    open: close,
    high: close + 0.3,
    low: close - 0.3,
    close,
  }));
  const securities = [
    { code: '000001', name: '金三角样例', market: 'SZ' },
    { code: '600000', name: '死三角样例', market: 'SH' },
    { code: '000002', name: '历史双向样例', market: 'SZ' },
    { code: '000003', name: '短历史样例', market: 'SZ' },
  ];
  const symbolBars = [
    golden,
    death,
    cycle,
    golden.map((bar, index) =>
      index < 176
        ? { kind: 'gap', date: bar.date, reason: 'missing-row' }
        : bar,
    ),
  ];
  const asOf = sessions.at(-1);
  const generated = '2026-09-10T10:00:00Z';
  const rows = securities.map((security, index) => {
    const bars = symbolBars[index].slice(-140);
    const analysis = analyzeTriangles({
      bars,
      params: defaultTriangleParams,
      asOf,
    });
    const last = bars.at(-1);
    return {
      security,
      latestClose: last.kind === 'bar' ? last.close : null,
      availability: analysis.availability,
      formations: analysis.formations.filter(
        (formation) =>
          formation.qualified &&
          ['active', 'observing'].includes(formation.state.kind),
      ),
    };
  });
  const screen = {
    schema: 'stock.triangles.daily.screen.v1',
    dataset_id: dataset,
    as_of: asOf,
    generated_at: generated,
    params: { ...defaultTriangleParams },
    sessions: sessions.slice(-140),
    coverage: {
      loadedDays: 140,
      requestedDays: 140,
      requestedHistory: 140,
      failedDates: [],
      calendarKnown: true,
      universeTotal: 4,
      completePriceSeries: 3,
      validPriceBars: 424,
    },
    rows,
  };
  const bundle = {
    schema: 'stock.triangles.daily.bundle.v1',
    dataset_id: dataset,
    as_of: asOf,
    sessions,
    rows: securities.map((security, index) => ({
      security,
      bars: symbolBars[index],
    })),
  };
  const manifest = {
    schema: 'stock.triangles.daily.manifest.v1',
    dataset_id: dataset,
    as_of: asOf,
    generated_at: generated,
    params: { ...defaultTriangleParams },
    history_sessions: 140,
    recent_sessions: 5,
    sessions: sessions.slice(-140),
    detail_sessions: sessions,
    universe_total: securities.length,
    source: { kind: 'synthetic-test-only' },
    rule_sha256: triangleDailyRuleSha256,
    screening: fileVersion('screening.json', screen),
    bundles: [
      {
        ...fileVersion('bundle-000.json', bundle),
        codes: securities.map((security) => security.code),
      },
    ],
  };
  const status = {
    schema: 'stock.triangles.daily.status.v1',
    state: 'COMMITTED',
    as_of: asOf,
    updated_at: generated,
    dataset_id: dataset,
    last_committed_date: asOf,
    message: 'Synthetic test publication.',
  };
  return { manifest, screen, bundle, status, securities };
}
