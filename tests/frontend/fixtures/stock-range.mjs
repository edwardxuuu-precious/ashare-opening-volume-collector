// Synthetic query fixtures. These values are invented test inputs, not market data.
// The calendar is fixed for the 60-date regression window; it is not a production
// trading calendar and must never be used by the collector or website.
const codes = [
  '000001', '600519', '920002', '000002',
  ...Array.from({ length: 28 }, (_, index) => String(100000 + index)),
];
const dates = [];
for (
  let instant = Date.UTC(2026, 5, 12);
  instant <= Date.UTC(2026, 8, 4);
  instant += 86_400_000
) {
  const date = new Date(instant);
  const isoDate = date.toISOString().slice(0, 10);
  if (date.getUTCDay() > 0 && date.getUTCDay() < 6 && isoDate !== '2026-06-19') {
    dates.push(isoDate);
  }
}

export function createStockRangeFixture() {
  const catalog = {
    asOf: '2026-09-04',
    source: 'synthetic-test-fixture',
    historicalMembership: false,
    total: codes.length,
    rows: codes.map((code, index) => ({ code, name: `合成测试股票 ${index + 1}` })),
  };
  const manifest = {
    generatedAt: '2026-09-05T00:00:00.000Z',
    source: 'synthetic-test-fixture',
    scope: 'sample',
    universeTotal: catalog.total,
    methodology: 'Invented volumes for deterministic query regression tests.',
    tradingDates: [...dates],
    dates: [...dates].reverse().map((date) => ({
      date,
      file: `${date}.json`,
      status: 'complete',
      total: codes.length,
      valid: codes.length,
      missing: 0,
      suspended: 0,
    })),
  };
  const fetchDay = async (entry) => {
    const dateIndex = dates.indexOf(entry.date);
    if (dateIndex < 0) throw new RangeError(`Unknown synthetic fixture date: ${entry.date}`);
    return {
      date: entry.date,
      generatedAt: manifest.generatedAt,
      source: manifest.source,
      scope: manifest.scope,
      universeTotal: catalog.total,
      total: codes.length,
      methodology: manifest.methodology,
      rows: catalog.rows.map((stock, stockIndex) => {
        const dailyVolume = 10_000_000 + stockIndex * 100_000;
        const first15Volume = stock.code === '000001' && entry.date === '2026-09-04'
          ? 1_888_569
          : 1_800_000 + dateIndex * 1_000 + stockIndex * 100;
        return {
          ...stock,
          date: entry.date,
          market: stock.code === '600519' ? 'SH' : stock.code === '920002' ? 'BJ' : 'SZ',
          first15Volume,
          dailyVolume,
          ratio: first15Volume / dailyVolume * 100,
          status: 'ok',
        };
      }),
    };
  };
  return { manifest, catalog, fetchDay };
}
