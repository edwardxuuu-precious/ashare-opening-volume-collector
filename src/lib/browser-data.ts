import { normalizeDownloadedRow } from './stock.ts';
import type { DayData, StockRow } from './stock.ts';

const displayFields = [
  'code',
  'name',
  'market',
  'first15Volume',
  'dailyVolume',
  'ratio',
  'pctChange',
  'amplitude',
  'open',
  'high',
  'low',
  'close',
  'priceStatus',
  'priceSourceProvider',
  'status',
  'sourceProvider',
  'specialStatus',
] as const;
const projectedRows = new WeakSet<StockRow>();
const projectedDays = new WeakSet<DayData>();

// S3 and API responses retain the full audit trail. Only the in-memory browser
// view drops unused histories, before the per-day cache or combined result holds it.
export function projectDayForBrowser(data: DayData): DayData {
  if (projectedDays.has(data)) return data;
  const rows = data.rows.map((original) => {
    if (projectedRows.has(original) && original.date === data.date)
      return original;
    const display: StockRow = {
      code: original.code,
      name: original.name,
      market: original.market,
      first15Volume: original.first15Volume,
      dailyVolume: original.dailyVolume,
      ratio: original.ratio,
      status: original.status,
      ...Object.fromEntries(
        displayFields
          .filter((key) => key in original)
          .map((key) => [key, original[key]]),
      ),
    };
    // Missing-data explanations are user-visible; retain only that compact
    // reason while still dropping server histories and reconciliation audits.
    if (original.status === 'missing' && typeof original.reason === 'string')
      display.reason = original.reason;
    display.date = data.date;
    const row = normalizeDownloadedRow(display);
    projectedRows.add(row);
    return row;
  });
  const result = { ...data, rows };
  projectedDays.add(result);
  return result;
}
