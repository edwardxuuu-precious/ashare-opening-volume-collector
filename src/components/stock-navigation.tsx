/* oxlint-disable next/no-html-link-for-pages -- Static CDN routes use document navigation. */
import './stock-navigation.css';
export function StockNavigation({active}:{active:'opening'|'chips'|'golden'|'trend'}) {
  return <nav className="stock-feature-nav" aria-label="主要功能"><a href="/" aria-current={active==='opening'?'page':undefined}>开盘量查询</a><a href="/chips" aria-current={active==='chips'?'page':undefined}>每日筹码筛选</a><a href="/golden" aria-current={active==='golden'?'page':undefined}>黄金画格核对</a><a href="/trend" aria-current={active==='trend'?'page':undefined}>趋势量价筛选</a></nav>;
}
