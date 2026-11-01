export type ReadoutCandle = { date: string; open: number | string; high: number | string; low: number | string; close: number | string };

export function priceChangeText(value: number | null): string {
  return value === null ? '待核实' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

export function priceChangeClass(value: number | null): 'candle-change-up' | 'candle-change-down' | undefined {
  return value === null || value === 0 ? undefined : value > 0 ? 'candle-change-up' : 'candle-change-down';
}

export function volumeText(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '待核实';
  return value >= 1e8 ? `${(value / 1e8).toFixed(2)}亿` : value >= 1e4 ? `${(value / 1e4).toFixed(2)}万` : value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

export function volumeUnitLabel(unit: string): string {
  const labels: Record<string, string> = { provider_native_consistent_ratio_only: '原生量（仅比较比例）', hands_100_shares: '手（100股）', shares: '股', SOURCE_UNKNOWN: '单位待核实', UNKNOWN: '单位待核实' };
  return labels[unit] ?? (unit || '单位待核实');
}

export function candleVolumeText(value: number | null, unit: string): string {
  return value === null || !Number.isFinite(value) ? '待核实' : `${volumeText(value)}${volumeUnitLabel(unit)}`;
}
