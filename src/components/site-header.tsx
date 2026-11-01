import type { ReactNode } from 'react';
import { AccountControls } from './auth-gate';
import { BrandIdentity } from './brand';
import { StockNavigation, type SiteSection } from './stock-navigation';
import { SiteLink } from './site-router';
import './site-header.css';

export function SiteHeader({ active, children }: { active?: SiteSection; children?: ReactNode }) {
  return <header className="stock-header chip-site-header cw-header">
    <SiteLink href="/" className="brand" aria-label="量序首页"><BrandIdentity /></SiteLink>
    <StockNavigation active={active} />
    <div className="stock-header-actions cw-header-actions">{children}<AccountControls /></div>
  </header>;
}
