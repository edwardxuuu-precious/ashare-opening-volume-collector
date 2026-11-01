import type { ReadoutCandle } from '@/lib/chart-readout';
import { candleVolumeText } from '@/lib/chart-readout';
import { CandleChange } from '@/components/candle-change';
import './chart-controls.css';

export function CandleReadout({ candle, percent, volume, volumeUnit, turnoverPercent, className = '' }: { candle: ReadoutCandle; percent: number | null; volume: number | null; volumeUnit: string; turnoverPercent?: number | null; className?: string }) {
  return <div className={`candle-readout ${className}`.trim()} aria-live="polite" aria-label="K线读数"><b data-field="date">{candle.date}</b><span data-field="open">开 {Number(candle.open).toFixed(2)}</span><span data-field="high">高 {Number(candle.high).toFixed(2)}</span><span data-field="low">低 {Number(candle.low).toFixed(2)}</span><span data-field="close">收 {Number(candle.close).toFixed(2)}</span><CandleChange percent={percent} /><span data-field="volume">量 {candleVolumeText(volume, volumeUnit)}</span>{turnoverPercent !== undefined && <span data-field="turnover">换手 {turnoverPercent === null ? '待核实' : `${turnoverPercent.toFixed(2)}%`}</span>}</div>;
}
