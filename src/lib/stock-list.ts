export const normalizeStockQuery = (query: string) => query.trim().toLowerCase();

export function stockListMatches(query: string, ...fields: string[]): boolean {
  return fields.join(' ').toLowerCase().includes(normalizeStockQuery(query));
}

export function nextStockListItem(keys: readonly string[], selected: string, direction: -1 | 1): { key: string; index: number } | null {
  if (!keys.length) return null;
  const current = keys.indexOf(selected);
  const index = current < 0 ? direction === 1 ? 0 : keys.length - 1 : current + direction;
  return index < 0 || index >= keys.length ? null : { key: keys[index], index };
}

export function inspectorTabIndex(key: string, index: number, length: number): number | null {
  if (!length) return null;
  if (key === 'Home') return 0;
  if (key === 'End') return length - 1;
  if (key === 'ArrowRight') return (index + 1) % length;
  if (key === 'ArrowLeft') return (index + length - 1) % length;
  return null;
}
