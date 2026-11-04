import { SiteLink } from './site-router';
import './stock-navigation.css';
export type SiteSection = 'opening' | 'chips' | 'golden' | 'trend' | 'concentration' | 'triangles';
export function StockNavigation({active}:{active?:SiteSection}) {
  const items: { section: SiteSection; href: string; label: string }[] = [
    { section: 'opening', href: '/', label: '开盘量查询' },
    { section: 'chips', href: '/chips', label: '每日筹码' },
    { section: 'concentration', href: '/concentration', label: '连续集中度' },
    { section: 'trend', href: '/trend', label: '趋势量价' },
    { section: 'triangles', href: '/triangles', label: '趋势三角' },
    { section: 'golden', href: '/golden/daily', label: '每日画线' },
  ];
  return <nav className="stock-feature-nav" aria-label="主要功能">{items.map(item=><SiteLink key={item.section} href={item.href} aria-current={active===item.section?'page':undefined}>{item.label}</SiteLink>)}</nav>;
}
