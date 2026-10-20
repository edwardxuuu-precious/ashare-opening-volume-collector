'use client';
import { useState } from 'react';
import { zhCN } from 'react-day-picker/locale';
import type { DateRange } from 'react-day-picker';
import { Calendar } from '@/components/ui/calendar';
import { Button } from '@/components/ui/button';
import { dateKey, localDate } from '@/lib/date-window';
import type { Filters } from '@/lib/stock';
export function DateCalendar({
  filters,
  tradingDates,
  savedDates,
  start,
  end,
  onChange,
  onRangePendingChange,
}: {
  filters: Filters;
  tradingDates: string[];
  savedDates: string[];
  start: string;
  end: string;
  onChange: (patch: Partial<Filters>) => void;
  onRangePendingChange: (pending: boolean) => void;
}) {
  const initial =
    filters.mode === 'multiple'
      ? [...filters.dates].sort().at(-1)
      : filters.mode === 'single'
        ? filters.date
        : filters.endDate || filters.date;
  const [month, setMonth] = useState(
    localDate(initial && initial >= start && initial <= end ? initial : end),
  );
  const [range, setRange] = useState<DateRange | undefined>(
    filters.date
      ? {
          from: localDate(filters.date),
          to: filters.endDate ? localDate(filters.endDate) : undefined,
        }
      : undefined,
  );
  // A saved range is display context. Every opening starts a new two-click
  // selection instead of letting DayPicker extend the saved range.
  const [rangeStep, setRangeStep] = useState<'start' | 'end' | 'complete'>(
    'start',
  );
  const [hoveredDay, setHoveredDay] = useState<Date | undefined>();
  const available = new Set(tradingDates);
  const orderedRange = (from: Date, to: Date) =>
    from <= to ? { from, to } : { from: to, to: from };
  const previewRange =
    rangeStep === 'end' && range?.from && hoveredDay
      ? orderedRange(range.from, hoveredDay)
      : range;
  const chooseRangeDay = (day: Date) => {
    const key = dateKey(day);
    if (key < start || key > end || !available.has(key)) return;
    setHoveredDay(undefined);
    if (rangeStep !== 'end' || !range?.from) {
      setRange({ from: day });
      setRangeStep('end');
      onRangePendingChange(true);
      return;
    }
    const next = orderedRange(range.from, day);
    setRange(next);
    setRangeStep('complete');
    onRangePendingChange(false);
    onChange({ date: dateKey(next.from), endDate: dateKey(next.to) });
  };
  const shared = {
    locale: zhCN,
    month,
    onMonthChange: setMonth,
    startMonth: localDate(start),
    endMonth: localDate(end),
    weekStartsOn: 1 as const,
    showOutsideDays: false,
    className: 'date-calendar',
    fixedWeeks: true,
    disabled: (date: Date) => {
      const key = dateKey(date);
      return key < start || key > end || !available.has(key);
    },
    modifiers: { saved: savedDates.map(localDate) },
    modifiersClassNames: { saved: 'saved-day' },
    labels: { labelPrevious: () => '上个月', labelNext: () => '下个月' },
  };
  return (
    <div className="calendar-selection">
      {filters.mode === 'range' ? (
        <div className="calendar-range-summary">
          <div className="calendar-range-dates">
            <div
              className="calendar-range-date"
              data-active={rangeStep === 'start'}
            >
              <span>1 · 开始日期</span>
              <strong>{range?.from ? dateKey(range.from) : '待选择'}</strong>
            </div>
            <div
              className="calendar-range-date"
              data-active={rangeStep === 'end'}
            >
              <span>2 · 结束日期</span>
              <strong>{range?.to ? dateKey(range.to) : '待选择'}</strong>
            </div>
          </div>
          <div className="calendar-selection-caption">
            <output aria-live="polite" aria-atomic="true">
              {rangeStep === 'start'
                ? '先点开始日期，再点结束日期'
                : rangeStep === 'end'
                  ? '请选择结束日期，可切换月份'
                  : '区间已选好，再点日期可重新选择'}
            </output>
            <Button
              variant="ghost"
              onClick={() => {
                setRange(undefined);
                setHoveredDay(undefined);
                setRangeStep('start');
                onRangePendingChange(true);
              }}
            >
              重新选择
            </Button>
          </div>
        </div>
      ) : (
        <div className="calendar-selection-caption">
          <span>
            {filters.mode === 'single'
              ? filters.date || '选择交易日'
              : `已选 ${filters.dates.length} 天`}
          </span>
          {filters.mode === 'multiple' && (
            <Button variant="ghost" onClick={() => onChange({ dates: [] })}>
              清空
            </Button>
          )}
        </div>
      )}
      {filters.mode === 'single' ? (
        <Calendar
          {...shared}
          mode="single"
          selected={filters.date ? localDate(filters.date) : undefined}
          onSelect={(d) => d && onChange({ date: dateKey(d) })}
        />
      ) : filters.mode === 'multiple' ? (
        <Calendar
          {...shared}
          mode="multiple"
          selected={filters.dates.map(localDate)}
          onSelect={(days) =>
            onChange({ dates: (days ?? []).map(dateKey).sort() })
          }
        />
      ) : (
        <Calendar
          {...shared}
          mode="range"
          selected={previewRange}
          onSelect={(_next, day) => chooseRangeDay(day)}
          onDayMouseEnter={(day, modifiers) => {
            if (rangeStep === 'end' && !modifiers.disabled) setHoveredDay(day);
          }}
          onDayMouseLeave={() => setHoveredDay(undefined)}
        />
      )}
      <div className="calendar-legend">
        <span>
          <i />
          已保存
        </span>
        <span>无标记：未保存</span>
      </div>
    </div>
  );
}
