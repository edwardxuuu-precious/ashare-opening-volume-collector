import './chart-controls.css';

export function ChartPeriodControl({ period, periods, onPeriodChange, availableCount, className = '' }: { period: number; periods: readonly number[]; onPeriodChange: (period: number) => void; availableCount: number; className?: string }) {
  return <fieldset className={`chart-period-control ${className}`.trim()} aria-label="图表周期"><span>日K</span>{periods.map(value => <button type="button" key={value} aria-pressed={period === value} onClick={() => onPeriodChange(value)}>{value}根</button>)}{availableCount < period && <small className="chart-available-count">可用 {availableCount} 根</small>}</fieldset>;
}
