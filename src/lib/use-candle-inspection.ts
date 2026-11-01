import { useRef, useState } from 'react';

export function useCandleInspection({ history, dates, defaultDate, contextDate, onInspectDate, onCommitDate }: { history: object; dates: readonly string[]; defaultDate: string; contextDate: string; onInspectDate?: (date: string | null) => void; onCommitDate?: (date: string) => void }) {
  const [inspection, setInspection] = useState<{ history: object; contextDate: string; date: string } | null>(null);
  const control = useRef<HTMLInputElement>(null);
  const activeDate = inspection?.history === history && inspection.contextDate === contextDate && dates.includes(inspection.date) ? inspection.date : null;
  const inspect = (date: string | null) => {
    setInspection(current => date === null ? null : current?.history === history && current.contextDate === contextDate && current.date === date ? current : { history, contextDate, date });
    onInspectDate?.(date);
  };
  const select = (date: string) => { if (onCommitDate) { inspect(null); onCommitDate(date); } else inspect(date); };
  return { activeDate, date: activeDate ?? defaultDate, inspect, select, clear: () => inspect(null), control, focus: () => control.current?.focus({ preventScroll: true }) };
}
