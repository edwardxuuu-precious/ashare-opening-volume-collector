'use client';

import type { ReactNode } from 'react';
import { ChevronDown, Search } from 'lucide-react';
import { priceChangeClass, priceChangeText } from '@/lib/chart-readout';
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from './ui/popover';
import './workbench.css';

export function WorkbenchPageBar({
  title,
  modeLabel,
  date,
  dateLabel = '筛选日',
  explanation,
  status,
  children,
}: {
  title: string;
  modeLabel?: string;
  date?: string | null;
  dateLabel?: string;
  explanation?: { label: string; content: ReactNode };
  status?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="wb-page-bar">
      <h1 className="wb-page-title">{title}</h1>
      <div className="wb-page-context">
        {modeLabel && <span className="wb-mode-label">{modeLabel}</span>}
        <span className="wb-date">
          <small>{dateLabel}</small>
          <time>{date ?? '—'}</time>
        </span>
        {explanation && <Popover>
          <PopoverTrigger className="wb-explanation-trigger">
            {explanation.label}<ChevronDown size={12} aria-hidden="true" />
          </PopoverTrigger>
          <PopoverContent className="wb-explanation" align="start" sideOffset={8}>
            <PopoverTitle>{explanation.label}</PopoverTitle>
            <div className="wb-explanation-body">{explanation.content}</div>
          </PopoverContent>
        </Popover>}
        {status && <output className="wb-context-status">{status}</output>}
      </div>
      {children && <div className="wb-page-actions">{children}</div>}
    </div>
  );
}

export function WorkbenchShell({
  sidebar,
  children,
  className = '',
  contentClassName = '',
}: {
  sidebar: ReactNode;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <div className={`wb-shell ${className}`}>
      {sidebar}
      <main className={`wb-workspace ${contentClassName}`}>{children}</main>
    </div>
  );
}

type ListFilter = {
  id: string;
  label: string;
  count: number | null;
  active: boolean;
  onSelect: () => void;
};
export function WorkbenchListHeader({
  title = '股票名单',
  count,
  search,
  filters,
  ready = true,
  unit = '只',
}: {
  unit?: string;
  title?: string;
  count: number | null;
  search: { value: string; label: string; onChange: (value: string) => void };
  filters: ListFilter[];
  ready?: boolean;
}) {
  return (
    <div className="wb-list-tools">
      <div className="wb-list-title">
        <h2>{title}</h2>
        {(!filters.length || search.value.trim()) && <span>{search.value.trim() ? '匹配 ' : ''}{count === null ? '—' : `${count.toLocaleString()}${unit}`}</span>}
      </div>
      <div className="wb-search">
        <Search size={14} aria-hidden="true" />
        <input
          type="search"
          aria-label={search.label}
          placeholder="搜索名称或代码"
          value={search.value}
          onChange={(event) => search.onChange(event.target.value)}
          disabled={!ready}
        />
      </div>
      <div className="wb-filters" aria-label="名单分类">
        {filters.map((filter) => (
          <button
            key={filter.id}
            type="button"
            aria-pressed={filter.active}
            disabled={!ready}
            onClick={filter.onSelect}
          >
            <span>{filter.label}</span>
            <b>{filter.count?.toLocaleString() ?? '—'}</b>
          </button>
        ))}
      </div>
    </div>
  );
}

export function StockDetailHeader({
  name,
  code,
  date,
  close,
  percent,
  quoteNote,
  basis = '不复权',
  badge,
  children,
  className = '',
}: {
  name: string;
  code: string;
  date: string;
  close: number | null;
  percent: number | null;
  quoteNote?: string;
  basis?: string;
  badge?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <header className={`wb-stock-header cw-stock-header ${className}`}>
      <div>
        <h2>{name}</h2>
        <div className="cw-stock-symbol">
          <span>{code}</span>
          <span>{basis}</span>
          {badge}
        </div>
        {children}
      </div>
      <div className="cw-quote">
        <strong
          data-quote-close={close ?? undefined}
          className={priceChangeClass(percent)}
        >
          {close?.toFixed(2) ?? '—'}
        </strong>
        <small>
          {quoteNote ?? (
            <>
              {date.slice(5)}收盘 ·{' '}
              <span className={priceChangeClass(percent)}>
                {priceChangeText(percent)}
              </span>
            </>
          )}
        </small>
      </div>
    </header>
  );
}

export function WorkbenchCheck({
  title,
  state,
  children,
}: {
  title: string;
  state: 'PASS' | 'FAIL' | 'UNKNOWN';
  children: ReactNode;
}) {
  return (
    <section className="wb-check tv-check">
      <div className="wb-check-heading tv-section-heading">
        <h3>{title}</h3>
        <span
          className={`wb-condition-state wb-condition-${state.toLowerCase()}`}
        >
          {state === 'PASS' ? '满足' : state === 'FAIL' ? '未满足' : '待核实'}
        </span>
      </div>
      {children}
    </section>
  );
}

export type WorkbenchReadState =
  | { kind: 'loading'; label?: string }
  | { kind: 'empty'; title: string; message?: string }
  | { kind: 'error'; title?: string; message: string; onRetry: () => void };
export function WorkbenchState({ state }: { state: WorkbenchReadState }) {
  if (state.kind !== 'loading')
    return (
      <section
        className="wb-state"
        role={state.kind === 'error' ? 'alert' : undefined}
        aria-live={state.kind === 'empty' ? 'polite' : undefined}
      >
        <div className="wb-state-mark" aria-hidden="true">
          {state.kind === 'error' ? '!' : '∅'}
        </div>
        <h2>
          {state.kind === 'error'
            ? (state.title ?? '暂时无法读取')
            : state.title}
        </h2>
        {'message' in state && state.message && <p>{state.message}</p>}
        {state.kind === 'error' && (
          <button className="wb-button" onClick={state.onRetry}>
            重新读取
          </button>
        )}
      </section>
    );
  return (
    <div className="wb-loading" aria-busy="true">
      <output className="wb-loading-label">
        {state.label ?? '正在读取行情…'}
      </output>
      <div className="wb-loading-heading">
        <i />
        <i />
      </div>
      <div className="wb-loading-meta">
        <i />
        <i />
        <i />
      </div>
      <div className="wb-loading-controls">
        <i />
        <i />
        <i />
        <i />
      </div>
      <div className="wb-loading-readout">
        <i />
        <i />
        <i />
        <i />
        <i />
      </div>
      <div className="wb-chart-placeholder">
        <div />
        <div />
        <div />
        <div />
        <div />
      </div>
      <div className="wb-loading-metrics">
        <i />
        <i />
        <i />
      </div>
    </div>
  );
}

export function WorkbenchListSkeleton() {
  return (
    <div className="wb-list-skeleton" aria-label="正在读取股票名单">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index}>
          <i />
          <i />
          <i />
        </div>
      ))}
    </div>
  );
}

export function WorkbenchListFooter({
  count,
  page,
  pageSize = 60,
  onPageChange,
  dateKeys = false,
}: {
  count: number;
  page: number;
  pageSize?: number;
  onPageChange: (page: number) => void;
  dateKeys?: boolean;
}) {
  return (
    <footer className="wb-list-footer">
      <div>
        <div className="wb-pagination">
          <button
            aria-label="上一页股票"
            disabled={page === 0}
            onClick={() => onPageChange(page - 1)}
          >
            ‹
          </button>
          <span>
            {page + 1} / {Math.max(1, Math.ceil(count / pageSize))}
          </span>
          <button
            aria-label="下一页股票"
            disabled={(page + 1) * pageSize >= count}
            onClick={() => onPageChange(page + 1)}
          >
            ›
          </button>
        </div>
      </div>
      <small>↑ ↓ 换股{dateKeys ? ' · ← → 换日期' : ''}</small>
    </footer>
  );
}
