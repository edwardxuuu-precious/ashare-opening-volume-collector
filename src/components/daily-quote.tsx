import type { StockRow } from '../lib/stock';

function formatPrice(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? value.toFixed(2)
    : '—';
}

export function hasDailyQuote(row: StockRow) {
  const values = [row.open, row.high, row.low, row.close];
  return (
    row.priceStatus === 'available' &&
    values.every(
      (value) =>
        typeof value === 'number' && Number.isFinite(value) && value > 0,
    ) &&
    row.low! <= row.open! &&
    row.open! <= row.high! &&
    row.low! <= row.close! &&
    row.close! <= row.high!
  );
}

export function DailyQuote({
  row,
  pair,
}: {
  row: StockRow;
  pair?: 'open-close' | 'high-low';
}) {
  if (pair) {
    if (!hasDailyQuote(row))
      return <span className="unavailable-value">— / —</span>;
    const [first, second] =
      pair === 'open-close' ? [row.open, row.close] : [row.high, row.low];
    return (
      <span className="price-pair">
        {formatPrice(first)} <span>/</span> {formatPrice(second)}
      </span>
    );
  }
  if (row.priceStatus === 'not_traded') {
    const delisted = row.specialStatus?.type === 'pending_delisting';
    return (
      <span className="daily-quote-unavailable">
        {delisted ? '退市无成交' : '停牌无成交'}
      </span>
    );
  }
  if (!hasDailyQuote(row)) {
    return <span className="unavailable-value">—</span>;
  }
  return (
    <span className="daily-quote-values">
      <span>
        开 <strong>{formatPrice(row.open)}</strong>　收{' '}
        <strong>{formatPrice(row.close)}</strong>
      </span>
      <span>
        高 <strong>{formatPrice(row.high)}</strong>　低{' '}
        <strong>{formatPrice(row.low)}</strong>
      </span>
    </span>
  );
}
