'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type AnchorHTMLAttributes,
  type ComponentType,
  type ReactNode,
} from 'react';
import { sitePagePath, type SitePath } from '@/lib/site-routes';

const pages = {
  '/': () => import('../app/page'),
  '/chips': () => import('../app/chips/page'),
  '/chips/calibration': () => import('../app/chips/calibration/page'),
  '/concentration': () => import('../app/concentration/page'),
  '/trend': () => import('../app/trend/page'),
  '/trend/research': () => import('../app/trend/research/page'),
  '/golden': () => import('../app/golden/page'),
  '/golden/daily': () => import('../app/golden/daily/page'),
  '/golden/video-space': () => import('../app/golden/video-space/page'),
  '/triangles': () => import('../app/triangles/page'),
} satisfies Record<SitePath, () => Promise<{ default: ComponentType }>>;
type PagePath = SitePath;
type Navigation = {
  open: (href: string) => void;
  prepare: (href: string) => void;
};
type View =
  | { kind: 'initial' }
  | { kind: 'page'; href: string; Page: ComponentType; sequence: number };
const NavigationContext = createContext<Navigation | null>(null);
const modules = new Map<PagePath, Promise<{ default: ComponentType }>>();

function load(path: PagePath) {
  const existing = modules.get(path);
  if (existing) return existing;
  const request = pages[path]().catch((error) => {
    modules.delete(path);
    throw error;
  });
  modules.set(path, request);
  return request;
}

export function SiteRouter({ children }: { children: ReactNode }) {
  const [view, setView] = useState<View>({ kind: 'initial' });
  const [pending, setPending] = useState(false),
    [failed, setFailed] = useState('');
  const sequence = useRef(0),
    alive = useRef(true);
  const navigate = useCallback(
    async (href: string, historyMode: 'push' | 'pop' = 'push') => {
      const url = new URL(href, window.location.href),
        path = sitePagePath(url.pathname);
      if (url.origin !== window.location.origin || !path) return;
      const ticket = ++sequence.current;
      setFailed('');
      if (historyMode === 'push' && url.href === location.href) {
        setPending(false);
        return;
      }
      setPending(true);
      try {
        const pageModule = await load(path);
        if (!alive.current || ticket !== sequence.current) return;
        const target = url.pathname + url.search + url.hash;
        if (
          historyMode === 'push' &&
          target !== location.pathname + location.search + location.hash
        )
          history.pushState(null, '', target);
        setView({
          kind: 'page',
          href: target,
          Page: pageModule.default,
          sequence: ticket,
        });
        setPending(false);
        window.scrollTo(0, 0);
      } catch {
        if (!alive.current || ticket !== sequence.current) return;
        setPending(false);
        if (historyMode === 'pop') {
          window.location.reload();
          return;
        }
        setFailed(href);
      }
    },
    [],
  );
  useEffect(() => {
    alive.current = true;
    const pop = () => {
      if (!sitePagePath(location.pathname)) return;
      void navigate(location.href, 'pop');
    };
    window.addEventListener('stock-page-popstate', pop);
    document.documentElement.setAttribute('data-stock-router', '');
    return () => {
      alive.current = false;
      document.documentElement.removeAttribute('data-stock-router');
      window.removeEventListener('stock-page-popstate', pop);
    };
  }, [navigate]);
  useEffect(() => {
    if (view.kind !== 'page') return;
    const heading = document.querySelector<HTMLElement>(
      'main h1, .wb-page-bar h1, main h2',
    );
    if (heading) {
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
    }
  }, [view]);
  const prepare = (href: string) => {
    const url = new URL(href, location.href),
      path = sitePagePath(url.pathname);
    if (url.origin === location.origin && path) void load(path).catch(() => {});
  };
  return (
    <NavigationContext.Provider
      value={{
        open: (href) => {
          void navigate(href);
        },
        prepare,
      }}
    >
      {pending && (
        <output
          className="site-navigation-progress"
          aria-label="正在切换页面"
        />
      )}
      {failed && (
        <div className="site-navigation-error" role="alert">
          页面未能打开。
          <button
            onClick={() => {
              void navigate(failed);
            }}
          >
            重试
          </button>
          <button aria-label="关闭提示" onClick={() => setFailed('')}>
            ×
          </button>
        </div>
      )}
      {view.kind === 'initial' ? (
        children
      ) : (
        <view.Page key={`${view.href}:${view.sequence}`} />
      )}
    </NavigationContext.Provider>
  );
}

export function SiteLink({
  href,
  onClick,
  onPointerEnter,
  onFocus,
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  const navigation = useContext(NavigationContext);
  return (
    <a
      {...props}
      href={href}
      onPointerEnter={(event) => {
        onPointerEnter?.(event);
        if (href && !event.currentTarget.hasAttribute('download'))
          navigation?.prepare(href);
      }}
      onFocus={(event) => {
        onFocus?.(event);
        if (href && !event.currentTarget.hasAttribute('download'))
          navigation?.prepare(href);
      }}
      onClick={(event) => {
        onClick?.(event);
        if (
          !navigation ||
          !href ||
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey ||
          event.currentTarget.hasAttribute('download') ||
          (event.currentTarget.target && event.currentTarget.target !== '_self')
        )
          return;
        const url = new URL(href, location.href);
        if (
          url.origin !== location.origin ||
          !sitePagePath(url.pathname) ||
          url.hash
        )
          return;
        event.preventDefault();
        navigation.open(url.href);
      }}
    >
      {children}
    </a>
  );
}
