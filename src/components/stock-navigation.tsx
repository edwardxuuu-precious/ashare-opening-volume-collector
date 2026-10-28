import Link from 'next/link';
import './stock-navigation.css';
export function StockNavigation({active}:{active:'opening'|'chips'|'golden'|'trend'}) {
  return <nav className="stock-feature-nav" aria-label="主要功能"><Link href="/" aria-current={active==='opening'?'page':undefined}>开盘量查询</Link><Link href="/chips" aria-current={active==='chips'?'page':undefined}>每日筹码筛选</Link><Link href="/golden" aria-current={active==='golden'?'page':undefined}>黄金画格核对</Link><Link href="/trend" aria-current={active==='trend'?'page':undefined}>趋势量价研究</Link></nav>;
}
