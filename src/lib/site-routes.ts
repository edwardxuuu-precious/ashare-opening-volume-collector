export const sitePaths = [
  '/',
  '/chips',
  '/chips/calibration',
  '/trend',
  '/trend/research',
  '/golden',
] as const;
export type SitePath = (typeof sitePaths)[number];

function isSitePath(path: string): path is SitePath {
  return sitePaths.some((route) => route === path);
}

export function sitePagePath(path: string): SitePath | null {
  const normalized = path.length > 1 ? path.replace(/\/$/, '') : path;
  return isSitePath(normalized) ? normalized : null;
}

export const siteHistoryBootstrap = `window.addEventListener('popstate',function(event){if(!document.documentElement.hasAttribute('data-stock-router'))return;var path=location.pathname.length>1?location.pathname.replace(/\\/$/,''):location.pathname;if(${JSON.stringify(sitePaths)}.indexOf(path)<0)return;event.stopImmediatePropagation();window.dispatchEvent(new Event('stock-page-popstate'));});`;
