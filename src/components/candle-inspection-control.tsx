import type { RefObject } from 'react';
import './chart-controls.css';

export function CandleInspectionControl({ dates, date, onDateChange, onBlur, inputRef }: { dates: readonly string[]; date: string; onDateChange: (date: string) => void; onBlur: () => void; inputRef: RefObject<HTMLInputElement | null> }) {
  const index = dates.indexOf(date);
  return <label className="candle-inspection-control"><span>查看K线日期</span><input className="candle-date-range" ref={inputRef} type="range" min={0} max={Math.max(0, dates.length - 1)} step={1} value={index < 0 ? Math.max(0, dates.length - 1) : index} disabled={!dates.length} aria-label="查看K线日期" aria-valuetext={date} title="左右方向键逐根查看，Home到首根，End到末根。" onChange={event=>{const selected=dates[event.currentTarget.valueAsNumber];if(selected)onDateChange(selected);}} onBlur={onBlur}/><span aria-hidden="true">{date}</span></label>;
}
