import type { ProjectionRow } from './golden-projection.ts';
import { stockListMatches } from './stock-list.ts';

type PresetSecurity =
  | { readonly kind: 'equity'; readonly id: string; readonly name: string }
  | { readonly kind: 'index'; readonly id: 'sh000001' | 'sz399006'; readonly name: string };

export const goldenPresets: readonly PresetSecurity[] = [
  { kind: 'equity', id: '600825', name: '新华传媒' },
  { kind: 'equity', id: '301101', name: '明月镜片' },
  { kind: 'equity', id: '300722', name: '新余国科' },
  { kind: 'equity', id: '300684', name: '中石科技' },
  { kind: 'equity', id: '600851', name: '海欣股份' },
  { kind: 'equity', id: '600199', name: '金种子酒' },
  { kind: 'equity', id: '300580', name: '贝斯特' },
  { kind: 'equity', id: '301413', name: '安培龙' },
  { kind: 'equity', id: '300969', name: '恒帅股份' },
  { kind: 'equity', id: '300434', name: '金石亚药' },
  { kind: 'equity', id: '002229', name: '鸿博股份' },
  { kind: 'equity', id: '300308', name: '中际旭创' },
  { kind: 'equity', id: '300502', name: '新易盛' },
  { kind: 'equity', id: '300394', name: '天孚通信' },
  { kind: 'equity', id: '300857', name: '协创数据' },
  { kind: 'index', id: 'sh000001', name: '上证指数' },
  { kind: 'index', id: 'sz399006', name: '创业板指数' },
];

export type GoldenListItem =
  | { kind: 'published'; security: PresetSecurity; row: ProjectionRow }
  | { kind: 'missing'; security: PresetSecurity; reason: 'not-published' | 'index-unavailable' };

export function goldenPresetItems(rows: readonly ProjectionRow[], search = ''): GoldenListItem[] {
  const byCode = new Map(rows.map(row => [row.code, row]));
  return goldenPresets.filter(security => stockListMatches(search, security.id, security.name)).map(security => {
    const row = byCode.get(security.id);
    return row ? { kind: 'published', security, row } : { kind: 'missing', security, reason: security.kind === 'index' ? 'index-unavailable' : 'not-published' };
  });
}
