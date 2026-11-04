export const PRICE_GEOMETRY = 'log-price.v1' as const;

export function priceScale({prices, top, bottom}: {prices: readonly number[]; top: number; bottom: number}) {
  if (!prices.length || !Number.isFinite(top) || !Number.isFinite(bottom) || bottom <= top || prices.some(p => !Number.isFinite(p) || p <= 0)) return null;
  const logs = prices.map(Math.log), low = Math.min(...logs), high = Math.max(...logs);
  const padding = Math.max((high - low) * .08, .002);
  const minLog = low - padding, maxLog = high + padding;
  const min = Math.exp(minLog), max = Math.exp(maxLog);
  if (!(min > 0 && Number.isFinite(min) && Number.isFinite(max) && max > min)) return null;
  return priceScaleFromBounds({min, max, top, bottom});
}
export function geometricLevel(low: number, high: number, ratio: number): number {
  if (![low, high, ratio].every(Number.isFinite) || low <= 0 || high <= 0) throw new Error('INVALID_PRICE_GEOMETRY');
  return Math.exp(Math.log(low) + ratio * (Math.log(high) - Math.log(low)));
}
export function reflectedPrice(origin: number, middle: number): number {
  if (![origin, middle].every(Number.isFinite) || origin <= 0 || middle <= 0) throw new Error('INVALID_PRICE_GEOMETRY');
  return middle / origin * middle;
}

export function priceScaleFromBounds({min, max, top, bottom}: {min:number; max:number; top:number; bottom:number}) {
  const minLog = Math.log(min), maxLog = Math.log(max);
  const y = (price: number) => bottom - (Math.log(price) - minLog) / (maxLog - minLog) * (bottom - top);
  const priceAt = (position: number) => Math.exp(minLog + (bottom - position) / (bottom - top) * (maxLog - minLog));
  return {min, max, top, bottom, y, priceAt, ticks: (count: number) => Array.from({length: count}, (_, i) => priceAt(bottom - (bottom - top) * i / Math.max(1, count - 1)))};
}
