import type {
  ConcentrationRow,
  ConcentrationSource,
} from './concentration-screen';
import type { TrendStock } from './trend-volume';

export type ConcentrationCandleTarget = Pick<
  ConcentrationSource,
  'dataset_id' | 'as_of'
> &
  Pick<ConcentrationRow, 'security_id' | 'name'>;
export type ConcentrationCandles = Pick<
  TrendStock,
  'history' | 'name' | 'volume_unit'
>;
const invalid = () => Error('日K线与当前股票或批次不一致，或行情格式不正确。');
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw invalid();
  return value as Record<string, unknown>;
}
function number(value: unknown): number {
  if (
    (typeof value !== 'string' && typeof value !== 'number') ||
    (typeof value === 'string' && !value.trim())
  )
    throw invalid();
  const result = Number(value);
  if (!Number.isFinite(result)) throw invalid();
  return result;
}
function day(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    Number.isFinite(Date.parse(value + 'T00:00:00Z')) &&
    new Date(value + 'T00:00:00Z').toISOString().slice(0, 10) === value
  );
}
export function parseConcentrationCandles(
  raw: unknown,
  target: ConcentrationCandleTarget,
): ConcentrationCandles {
  const frame = object(raw);
  if (
    frame.schema !== 'stock.chips.market.stock.v1' ||
    frame.dataset_id !== target.dataset_id ||
    frame.as_of !== target.as_of ||
    frame.security_id !== target.security_id ||
    frame.name !== target.name ||
    !Array.isArray(frame.history) ||
    frame.history.length > 500
  )
    throw invalid();
  let previous = '';
  const history = frame.history.map((input: unknown) => {
    const bar = object(input);
    if (!day(bar.date) || bar.date <= previous || bar.date > target.as_of)
      throw invalid();
    previous = bar.date;
    const open = number(bar.open),
      high = number(bar.high),
      low = number(bar.low),
      close = number(bar.close),
      volume = number(bar.volume_shares);
    if (
      low <= 0 ||
      high < low ||
      open < low ||
      open > high ||
      close < low ||
      close > high ||
      volume < 0
    )
      throw invalid();
    return { date: bar.date, open, high, low, close, volume };
  });
  return { history, name: target.name, volume_unit: 'shares' };
}
