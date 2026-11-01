'use client';
import { useEffect, useRef, type RefObject } from 'react';
import { chipNavigationKey } from '@/lib/chip-keyboard';
import { nextStockListItem } from '@/lib/stock-list';

export function useStockListNavigation({ keys, selected, onSelect, listRef, page = 0, enabled = true }: {
  keys: readonly string[];
  selected: string;
  onSelect: (key: string, index: number) => void;
  listRef: RefObject<HTMLElement | null>;
  page?: number;
  enabled?: boolean;
}) {
  const focusKey = useRef<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    const navigate = (event: KeyboardEvent) => {
      const key = chipNavigationKey(event), list = listRef.current;
      if ((key !== 'ArrowUp' && key !== 'ArrowDown') || !keys.length) return;
      const target = event.target instanceof Element ? event.target : null;
      const button = target?.closest('button,a,[role="button"]');
      if (button && (!list?.contains(button) || !button.hasAttribute('data-stock-key'))) return;
      event.preventDefault();
      const next = nextStockListItem(keys, selected, key === 'ArrowUp' ? -1 : 1);
      if (!next) return;
      if (target && list?.contains(target)) focusKey.current = next.key;
      onSelect(next.key, next.index);
    };
    window.addEventListener('keydown', navigate);
    return () => window.removeEventListener('keydown', navigate);
  }, [keys, selected, onSelect, listRef, enabled]);
  useEffect(() => {
    const list = listRef.current;
    const row = list && [...list.querySelectorAll<HTMLButtonElement>('[data-stock-key]')].find(item => item.dataset.stockKey === selected);
    if (!list || !row) return;
    const bounds = list.getBoundingClientRect(), item = row.getBoundingClientRect();
    if (item.top < bounds.top) list.scrollTop += item.top - bounds.top;
    else if (item.bottom > bounds.bottom) list.scrollTop += item.bottom - bounds.bottom;
    if (focusKey.current === selected) {
      row.focus({ preventScroll: true });
      focusKey.current = null;
    }
  }, [selected, page, keys, listRef]);
}
