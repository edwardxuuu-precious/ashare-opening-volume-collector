'use client';
import { useEffect, useRef, useState } from 'react';
import { trendPrefix, type TrendStock, type TrendScreen } from '@/lib/trend-volume';
import { candleReturn } from '@/lib/candle-quote';
import { CandleInspectionControl } from '@/components/candle-inspection-control';
import { CandleReadout } from '@/components/candle-readout';
import { ChartPeriodControl } from '@/components/chart-period-control';
import { volumeText, volumeUnitLabel } from '@/lib/chart-readout';
import { candleIndexAtPoint } from '@/lib/chart-inspection';
import { useCandleInspection } from '@/lib/use-candle-inspection';

export { volumeText, volumeUnitLabel };

export function TrendChart({ stock, date, screen, lengths = [60, 120, 250, 500], initialPeriod = 60, periodValue, onPeriodChange }: { stock: Pick<TrendStock, 'history' | 'name' | 'volume_unit'>; date: string; screen: TrendScreen | null; lengths?: number[]; initialPeriod?: number; periodValue?: number; onPeriodChange?: (period: number) => void }) {
  const [localPeriod, setPeriod] = useState(initialPeriod), period = periodValue ?? localPeriod;
  const chart = useRef<SVGSVGElement>(null), [size, setSize] = useState({ width: 800, height: 300 });
  const bars = trendPrefix(stock.history, date, period);
  const inspection = useCandleInspection({ history: stock.history, dates: bars.map(bar => bar.date), defaultDate: bars.at(-1)?.date ?? date, contextDate: date });
  const hovered = bars.find(bar => bar.date === inspection.activeDate);
  const selected = hovered ?? bars.at(-1), unit = volumeUnitLabel(stock.volume_unit);
  const hasBars = bars.length > 0;
  useEffect(() => {
    const node = chart.current;
    if (!node) return;
    const measure = () => {
      const rect = node.getBoundingClientRect(), width = Math.round(rect.width), height = Math.round(rect.height);
      if (width > 0 && height > 0) setSize(current => current.width === width && current.height === height ? current : { width, height });
    };
    measure();
    if (typeof ResizeObserver === 'undefined') { window.addEventListener('resize', measure); return () => window.removeEventListener('resize', measure); }
    const observer = new ResizeObserver(measure); observer.observe(node); return () => observer.disconnect();
  }, [hasBars]);
  if (!selected || !bars.length) return <div className="tv-chart-empty">暂无可展示的行情。</div>;
  const W = size.width, H = size.height, left = W < 600 ? 10 : 22, right = W - 70, top = 24, volumeBottom = H - 32;
  const volumeTop = volumeBottom - Math.round(H * .2), volumeDivider = volumeTop - 22, bottom = volumeDivider - 18;
  const rawMin = Math.min(...bars.map(b => b.low)), rawMax = Math.max(...bars.map(b => b.high)), pad = Math.max((rawMax - rawMin) * .065, .02);
  const min = Math.max(.001, rawMin - pad), max = rawMax + pad, maxVolume = Math.max(1, ...bars.map(b => b.volume));
  const step = (right - left) / bars.length, width = Math.max(.45, Math.min(12, step * .62));
  const x = (i: number) => left + step * (i + .5), y = (price: number) => bottom - (price - min) / (max - min) * (bottom - top);
  const vy = (volume: number) => volumeBottom - volume / maxVolume * (volumeBottom - volumeTop);
  const recent = new Set(screen?.shrink_gate.recent_dates), reference = new Set(screen?.shrink_gate.reference_dates);
  const index = bars.findIndex(b => b.date === selected.date);
  const tickCount = Math.max(2, Math.min(6, Math.floor((right - left) / 72) + 1));
  const dateTicks = new Set(Array.from({ length: tickCount }, (_, i) => Math.round(i * (bars.length - 1) / (tickCount - 1))));
  function inspectPoint(event:React.PointerEvent<SVGSVGElement>,focus=false) { const r=event.currentTarget.getBoundingClientRect(),pointX=(event.clientX-r.left)/r.width*W,pointY=(event.clientY-r.top)/r.height*H,i=candleIndexAtPoint({x:pointX,y:pointY,left,right,top,bottom:volumeBottom,count:bars.length});if(focus&&i!==null){event.preventDefault();inspection.focus();}inspection.inspect(i===null?null:bars[i].date); }
  return <section className="tv-chart" aria-label="截至观察日的行情">
    <div className="tv-chart-toolbar"><ChartPeriodControl period={period} periods={lengths} availableCount={bars.length} onPeriodChange={value=>{setPeriod(value);onPeriodChange?.(value);inspection.clear();}}/><small>不复权 · 截至 {date}</small></div>
    <CandleReadout className="tv-candle-readout" candle={selected} percent={candleReturn(stock.history, selected.date)} volume={selected.volume} volumeUnit={stock.volume_unit}/>
    <CandleInspectionControl dates={bars.map(bar=>bar.date)} date={selected.date} onDateChange={inspection.select} onBlur={inspection.clear} inputRef={inspection.control}/>
    <div className="tv-chart-scroll"><svg className="candle-inspection" ref={chart} viewBox={`0 0 ${W} ${H}`} aria-labelledby="tv-chart-title" onPointerMove={inspectPoint} onPointerDown={event=>inspectPoint(event,true)} onPointerCancel={inspection.clear} onPointerLeave={event=>{if(event.pointerType!=='touch')inspection.clear();}}><title id="tv-chart-title">{`${stock.name}截至${date}的日K线和成交量，未展示此后的K线`}</title>
      <rect width={W} height={H} fill="white" />
      {[0, 1, 2, 3, 4, 5].map(i => { const p = min + (max - min) * i / 5, yy = y(p); return <g key={i}><line x1={left} x2={right} y1={yy} y2={yy} stroke="#e8ecf2" strokeDasharray="3 5" /><text x={right + 12} y={yy + 4} fill="#7a8698" fontSize="11">{p.toFixed(2)}</text></g>; })}
      {bars.map((bar, i) => {
        const color = bar.close >= bar.open ? 'var(--ui-rise)' : 'var(--ui-fall)', setup = bar.date === screen?.setup_date;
        return <g key={bar.date} data-candle-date={bar.date}>
          {(recent.has(bar.date) || reference.has(bar.date)) && <rect x={x(i) - step / 2} y={volumeTop - 6} width={step} height={volumeBottom - volumeTop + 6} fill="#345cd6" opacity={recent.has(bar.date) ? '.1' : '.035'} />}
          {setup && <><line x1={x(i)} x2={x(i)} y1={top} y2={volumeBottom} stroke="#345cd6" strokeDasharray="3 4" opacity=".5" /><text x={Math.min(right - 70, Math.max(left, x(i) - 20))} y={top - 7} fontSize="10" fill="#345cd6">准备 {bar.date.slice(5)}</text></>}
          <line x1={x(i)} x2={x(i)} y1={y(bar.high)} y2={y(bar.low)} stroke={color} />
          <rect x={x(i) - width / 2} y={Math.min(y(bar.open), y(bar.close))} width={width} height={Math.max(1, Math.abs(y(bar.open) - y(bar.close)))} fill={color} />
          <rect data-volume-date={bar.date} x={x(i) - width / 2} y={vy(bar.volume)} width={width} height={volumeBottom - vy(bar.volume)} fill={color} opacity=".75" />
          <rect x={x(i) - step / 2} y={top} width={step} height={volumeBottom - top} fill="transparent"><title>{`${bar.date} 收盘 ${bar.close} · 成交量 ${bar.volume} ${unit}`}</title></rect>
          {dateTicks.has(i) && <text x={x(i)} y={H - 14} textAnchor={i === 0 ? 'start' : i === bars.length - 1 ? 'end' : 'middle'} fontSize="10" fill="#7a8698">{bar.date.slice(5)}</text>}
        </g>;
      })}
      <line x1={left} x2={right} y1={volumeDivider} y2={volumeDivider} stroke="#e5eaf0" />
      <text x={left} y={volumeDivider + 14} fontSize="10" fill="#7a8698">成交量（{unit}）</text>
      <text x={right + 12} y={volumeDivider + 14} fontSize="10" fill="#7a8698">{volumeText(maxVolume)}</text>
      {screen?.baseline_mean !== null && screen?.baseline_mean !== undefined && screen.baseline_mean <= maxVolume && <g><line x1={left} x2={right} y1={vy(screen.baseline_mean)} y2={vy(screen.baseline_mean)} stroke="#345cd6" strokeDasharray="4 4" opacity=".7" /><text x={right + 10} y={vy(screen.baseline_mean) + 4} fontSize="10" fill="#345cd6">基准 B</text></g>}
      {hovered && <line x1={x(index)} x2={x(index)} y1={top} y2={volumeBottom} stroke="#65758d" strokeDasharray="3 4" opacity=".45" pointerEvents="none" />}
    </svg></div>
    <div className="tv-chart-legend"><span><i className="tv-legend-recent" />准备期3日量</span><span><i className="tv-legend-reference" />此前10日量</span></div>
  </section>;
}
