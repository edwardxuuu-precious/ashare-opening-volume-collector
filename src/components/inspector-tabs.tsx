'use client';
import type { ReactNode } from 'react';
import { inspectorTabIndex } from '@/lib/stock-list';

export function InspectorTabs<T extends string>({ tabs, value, onChange, idPrefix, label, className }: {
  tabs: readonly { id: T; label: ReactNode }[];
  value: T;
  onChange: (value: T) => void;
  idPrefix: string;
  label: string;
  className?: string;
}) {
  return <div className={className} role="tablist" aria-label={label}>
    {tabs.map((tab, index) => <button key={tab.id} type="button" role="tab" id={`${idPrefix}-${tab.id}-tab`} aria-controls={`${idPrefix}-${tab.id}`} aria-selected={value === tab.id} tabIndex={value === tab.id ? 0 : -1} onFocus={event => event.currentTarget.scrollIntoView({block:'nearest',inline:'nearest'})} onClick={() => onChange(tab.id)} onKeyDown={event => {
      if (event.defaultPrevented || event.nativeEvent.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const next = inspectorTabIndex(event.key, index, tabs.length);
      if (next === null) return;
      event.preventDefault(); event.stopPropagation();
      onChange(tabs[next].id);
      event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
    }}>{tab.label}</button>)}
  </div>;
}
