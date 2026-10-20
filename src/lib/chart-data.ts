import { normalizeDownloadedRow, type StockRow } from './stock.ts';
export type TrendSeries = { key: string; name: string; code?: string };
export type TrendPoint = {
  date: string;
  validCount: number;
  [key: string]: string | number | null;
};
export function buildTrend(
  rows: StockRow[],
  dates: string[],
  symbols: string[] | null,
) {
  const days = [...new Set(dates)].sort();
  const individual = symbols !== null && symbols.length <= 5;
  const series: TrendSeries[] = individual
    ? [...new Set(symbols)].map((code) => ({
        key: 's' + code,
        code,
        name: rows.find((r) => r.code === code)?.name ?? code,
      }))
    : [{ key: 'median', name: '每日占比中位数' }];
  const byDate = new Map<string, StockRow[]>();
  for (const original of rows) {
    const row = normalizeDownloadedRow(original);
    if (!row.date) continue;
    const group = byDate.get(row.date) ?? [];
    group.push(row);
    byDate.set(row.date, group);
  }
  const points: TrendPoint[] = days.map((date) => {
    const current = byDate.get(date) ?? [];
    const valid = current.filter(
      (r) => r.ratio !== null && Number.isFinite(r.ratio),
    );
    const point: TrendPoint = { date, validCount: valid.length };
    if (individual) {
      const values = new Map(current.map((r) => [r.code, r.ratio]));
      for (const item of series) {
        const value = values.get(item.code!);
        point[item.key] =
          value !== null && value !== undefined && Number.isFinite(value)
            ? value
            : null;
      }
    } else {
      const ratios = valid.map((r) => r.ratio as number).sort((a, b) => a - b);
      point.median = ratios.length
        ? (ratios[Math.floor((ratios.length - 1) / 2)] +
            ratios[Math.floor(ratios.length / 2)]) /
          2
        : null;
    }
    return point;
  });
  let maximum = 0;
  for (const point of points)
    for (const item of series) {
      const value = point[item.key];
      if (typeof value === 'number' && Number.isFinite(value))
        maximum = Math.max(maximum, value);
    }
  const axisTop = Math.min(100, Math.max(20, Math.ceil(maximum / 10) * 10));
  return { points, series, individual, axisTop };
}
