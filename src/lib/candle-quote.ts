type CandleClose = { date: string; close: string | number };

function decimalParts(value: CandleClose['close']) {
  const text = value.toString().trim();
  if (!Number.isFinite(Number(text)) || Number(text) <= 0 || !/^\+?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) return null;
  const [coefficient, exponent = '0'] = text.toLowerCase().replace(/^\+/, '').split('e');
  const [whole, fraction = ''] = coefficient.split('.');
  return { integer: BigInt(whole + fraction), scale: fraction.length - Number(exponent) };
}

export function candleReturn(history: readonly CandleClose[], date: string): number | null {
  const index = history.findIndex(bar => bar.date === date);
  if (index <= 0) return null;
  const previous = decimalParts(history[index - 1].close), current = decimalParts(history[index].close);
  if (!previous || !current) return null;
  const scale = Math.max(previous.scale, current.scale);
  const base = previous.integer * BigInt(10) ** BigInt(scale - previous.scale);
  const delta = current.integer * BigInt(10) ** BigInt(scale - current.scale) - base;
  // Match the backend's decimal ratio before converting it to a display number.
  const numerator = (delta < 0 ? -delta : delta) * BigInt(100), precision = BigInt(10) ** BigInt(40);
  const whole = numerator / base, fraction = (numerator % base * precision / base).toString().padStart(40, '0');
  const percent = Number(`${delta < 0 ? '-' : ''}${whole}.${fraction}`);
  return Number.isFinite(percent) ? percent : null;
}
