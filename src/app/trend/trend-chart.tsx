'use client';
import { useState } from 'react';
import { trendPrefix, type TrendStock, type TrendScreen } from '@/lib/trend-volume';

export const volumeText = (value: number | null) => value === null ? '待核实' : value >= 1e8 ? `${(value / 1e8).toFixed(2)}亿` : value >= 1e4 ? `${(value / 1e4).toFixed(2)}万` : value.toLocaleString('zh-CN', { maximumFractionDigits: 1 });
export function volumeUnitLabel(unit: string): string {
  const labels: Record<string, string> = { provider_native_consistent_ratio_only: '原生量（仅比较比例）', hands_100_shares: '手（100股）', SOURCE_UNKNOWN: '单位待核实', UNKNOWN: '单位待核实' };
  return labels[unit] ?? unit;
}

export function TrendChart({ stock, date, screen }: { stock: TrendStock; date: string; screen: TrendScreen | null }) {
  const [period, setPeriod] = useState(60), [hoverDate, setHoverDate] = useState<string | null>(null);
  const bars = trendPrefix(stock.history, date, period), selected = bars.find(b => b.date === hoverDate) ?? bars.at(-1), unit = volumeUnitLabel(stock.volume_unit);
  if (!selected || !bars.length) return <div className="tv-chart-empty">此样本未取得可展示的行情，保留数据不足状态。</div>;
  const W = 800, H = 350, left = 14, right = 730, top = 22, bottom = 225, volumeTop = 255, volumeBottom = 318;
  const rawMin = Math.min(...bars.map(b => b.low)), rawMax = Math.max(...bars.map(b => b.high)), pad = (rawMax - rawMin || rawMax * .03) * .12;
  const min = rawMin - pad, max = rawMax + pad, maxVolume = Math.max(1, ...bars.map(b => b.volume));
  const step = (right - left) / bars.length, width = Math.max(2, Math.min(9, step * .66));
  const x = (i: number) => left + step * (i + .5), y = (price: number) => bottom - (price - min) / (max - min) * (bottom - top);
  const vy = (volume: number) => volumeBottom - volume / maxVolume * (volumeBottom - volumeTop);
  const recent = new Set(screen?.shrink_gate.recent_dates), reference = new Set(screen?.shrink_gate.reference_dates);
  const index = bars.findIndex(b => b.date === selected.date);
  return <section className="tv-chart" aria-label="截至观察日的行情">
    <div className="tv-chart-toolbar"><span>日K线 / 成交量</span><div className="tv-segment" aria-label="图表长度">{[35, 60, 120].map(n => <button key={n} aria-pressed={period === n} onClick={() => setPeriod(n)}>{n}根</button>)}</div><small>不复权 · 截至 {date}</small></div>
    <div className="tv-candle-readout" aria-live="polite"><b>{selected.date}</b><span>开 {selected.open.toFixed(2)}</span><span>高 {selected.high.toFixed(2)}</span><span>低 {selected.low.toFixed(2)}</span><span>收 {selected.close.toFixed(2)}</span><span>量 {volumeText(selected.volume)} {unit}</span></div>
    <div className="tv-chart-scroll"><svg viewBox={`0 0 ${W} ${H}`} aria-labelledby="tv-chart-title" onPointerLeave={() => setHoverDate(null)}><title id="tv-chart-title">{`${stock.name}截至${date}的日K线和成交量，未展示此后的K线`}</title>
      <rect width={W} height={H} fill="white" />
      {[0, 1, 2, 3, 4].map(i => { const p = min + (max - min) * i / 4, yy = y(p); return <g key={i}><line x1={left} x2={right} y1={yy} y2={yy} stroke="#e8ecf2" strokeDasharray="3 5" /><text x={right + 12} y={yy + 4} fill="#7a8698" fontSize="11">{p.toFixed(2)}</text></g>; })}
      {bars.map((bar, i) => {
        const color = bar.close >= bar.open ? '#cb5964' : '#198d78', setup = bar.date === screen?.setup_date;
        return <g key={bar.date} data-candle-date={bar.date} onPointerMove={() => setHoverDate(bar.date)}>
          {(recent.has(bar.date) || reference.has(bar.date)) && <rect x={x(i) - step / 2} y={volumeTop - 6} width={step} height={volumeBottom - volumeTop + 6} fill="#345cd6" opacity={recent.has(bar.date) ? '.1' : '.035'} />}
          {setup && <><line x1={x(i)} x2={x(i)} y1={top} y2={volumeBottom} stroke="#345cd6" strokeDasharray="3 4" opacity=".5" /><text x={Math.min(right - 70, Math.max(left, x(i) - 20))} y={top - 7} fontSize="10" fill="#345cd6">准备 {bar.date.slice(5)}</text></>}
          <line x1={x(i)} x2={x(i)} y1={y(bar.high)} y2={y(bar.low)} stroke={color} />
          <rect x={x(i) - width / 2} y={Math.min(y(bar.open), y(bar.close))} width={width} height={Math.max(1, Math.abs(y(bar.open) - y(bar.close)))} fill={color} />
          <rect data-volume-date={bar.date} x={x(i) - width / 2} y={vy(bar.volume)} width={width} height={volumeBottom - vy(bar.volume)} fill={color} opacity=".75" />
          <rect x={x(i) - step / 2} y={top} width={step} height={volumeBottom - top} fill="transparent"><title>{bar.date} 收盘 {bar.close} · 成交量 {bar.volume} {unit}</title></rect>
          {(i === 0 || i === bars.length - 1 || i % Math.max(1, Math.floor(bars.length / 5)) === 0) && <text x={x(i)} y={H - 10} textAnchor={i === 0 ? 'start' : i === bars.length - 1 ? 'end' : 'middle'} fontSize="10" fill="#7a8698">{bar.date.slice(5)}</text>}
        </g>;
      })}
      <line x1={left} x2={right} y1={volumeTop - 10} y2={volumeTop - 10} stroke="#e5eaf0" />
      <text x={left} y={volumeTop - 16} fontSize="10" fill="#7a8698">成交量（{unit}）</text>
      <text x={right + 12} y={volumeTop + 4} fontSize="10" fill="#7a8698">{volumeText(maxVolume)}</text>
      {screen?.baseline_mean !== null && screen?.baseline_mean !== undefined && screen.baseline_mean <= maxVolume && <g><line x1={left} x2={right} y1={vy(screen.baseline_mean)} y2={vy(screen.baseline_mean)} stroke="#345cd6" strokeDasharray="4 4" opacity=".7" /><text x={right + 10} y={vy(screen.baseline_mean) + 4} fontSize="10" fill="#345cd6">基准 B</text></g>}
      {hoverDate && <line x1={x(index)} x2={x(index)} y1={top} y2={volumeBottom} stroke="#65758d" strokeDasharray="3 4" opacity=".45" pointerEvents="none" />}
    </svg></div>
    <div className="tv-chart-legend"><span><i className="tv-legend-recent" />准备期3日量</span><span><i className="tv-legend-reference" />此前10日量</span><span className="tv-muted">仅显示观察日及以前行情</span></div>
  </section>;
}
