'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type SubmitEvent,
  type ReactNode,
} from 'react';
import {
  ArrowRight,
  ChevronDown,
  CircleHelp,
  LoaderCircle,
  LogOut,
} from 'lucide-react';
import { readResponse } from '../lib/read-request.ts';
import { invalidateAuthRequests } from '../lib/auth-fetch.ts';
import { BrandIdentity } from './brand';
import { SiteHeader } from './site-header';
import { SiteRouter } from './site-router';
import {
  WorkbenchPageBar,
  WorkbenchShell,
  WorkbenchState,
  WorkbenchListSkeleton,
} from './workbench';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

const AccountContext = createContext<{
  user: string;
  busy: boolean;
  logout: () => void;
  error: string;
} | null>(null);

export function useAccount() {
  return useContext(AccountContext);
}

export function AccountControls() {
  const account = useContext(AccountContext);
  if (!account) return null;
  return (
    <div className="account-controls">
      <DropdownMenu>
        <DropdownMenuTrigger
          className="account-menu-trigger"
          aria-label="账户菜单"
          disabled={account.busy}
        >
          <span className="account-avatar" aria-hidden="true">
            {account.user.slice(0, 1).toLocaleUpperCase()}
          </span>
          <ChevronDown size={12} aria-hidden="true" />
        </DropdownMenuTrigger>
        <DropdownMenuContent
          className="account-menu"
          align="end"
          sideOffset={8}
        >
          <DropdownMenuGroup>
            <DropdownMenuLabel className="account-menu-user">
              {account.user}
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={account.logout} disabled={account.busy}>
              <LogOut size={15} aria-hidden="true" />
              退出登录
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      {account.error && <p role="alert">{account.error}</p>}
    </div>
  );
}

const api = (process.env.NEXT_PUBLIC_AUTH_API || '').replace(/\/$/, '');

async function readAuthResult(response: Response) {
  const value: unknown = await response.json();
  if (!value || typeof value !== 'object')
    throw Error('登录服务返回异常，请重试。');
  return {
    username:
      'username' in value &&
      typeof value.username === 'string' &&
      value.username.trim()
        ? value.username
        : '',
    error:
      'error' in value && typeof value.error === 'string' ? value.error : '',
  };
}

function AuthBackground() {
  return (
    <div className="auth-backdrop" aria-hidden="true">
      <div className="auth-ribbon auth-ribbon-primary" />
      <div className="auth-ribbon auth-ribbon-light" />
      <div className="auth-ribbon auth-ribbon-edge" />
    </div>
  );
}

function AuthBrand() {
  return (
    <header className="auth-header">
      <div className="auth-brand">
        <BrandIdentity />
      </div>
    </header>
  );
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<string | null>(null);
  const [checking, setChecking] = useState(Boolean(api));
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [sessionRetry, setSessionRetry] = useState(0);
  const [sessionCheckFailed, setSessionCheckFailed] = useState(false);
  const mutationBusy = useRef(false);
  const mounted = useRef(false);
  const sessionCheck = useRef<{
    ticket: number;
    controller: AbortController | null;
  }>({ ticket: 0, controller: null });
  const invalidateSessionCheck = useCallback(() => {
    sessionCheck.current.controller?.abort();
    sessionCheck.current = {
      ticket: sessionCheck.current.ticket + 1,
      controller: null,
    };
  }, []);
  useEffect(() => {
    mounted.current = true;
    const expired = () => {
      invalidateSessionCheck();
      invalidateAuthRequests();
      setUser(null);
      setChecking(false);
      setSessionCheckFailed(false);
      setError('登录已过期，请重新登录。');
    };
    window.addEventListener('stock-session-expired', expired);
    return () => {
      mounted.current = false;
      invalidateSessionCheck();
      window.removeEventListener('stock-session-expired', expired);
    };
  }, [invalidateSessionCheck]);
  useEffect(() => {
    if (!api || mutationBusy.current) return;
    invalidateSessionCheck();
    const controller = new AbortController();
    const ticket = sessionCheck.current.ticket;
    sessionCheck.current.controller = controller;
    const isCurrent = () =>
      sessionCheck.current.ticket === ticket && !controller.signal.aborted;
    readResponse(
      api + '/auth/me',
      { credentials: 'include', signal: controller.signal, cache: 'no-store' },
      async (r) => {
        if (!r.ok) {
          await r.body?.cancel().catch(() => {});
          if (r.status === 401) return null;
          throw Error(`登录状态读取失败，请重试。（HTTP ${r.status}）`);
        }
        return readAuthResult(r);
      },
    )
      .then((result) => {
        if (isCurrent()) setUser(result?.username || null);
      })
      .catch((e) => {
        if (isCurrent() && !(e instanceof Error && e.name === 'AbortError')) {
          setSessionCheckFailed(true);
          setError(
            e instanceof Error ? e.message : '无法连接登录服务，请稍后重试。',
          );
        }
      })
      .finally(() => {
        if (isCurrent()) setChecking(false);
      });
    return () => {
      controller.abort();
      if (sessionCheck.current.controller === controller)
        sessionCheck.current.controller = null;
    };
  }, [sessionRetry, invalidateSessionCheck]);
  async function login(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mutationBusy.current) return;
    mutationBusy.current = true;
    invalidateSessionCheck();
    setChecking(false);
    setBusy(true);
    setError('');
    setSessionCheckFailed(false);
    try {
      const response = await fetch(api + '/auth/login', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      const result = await readAuthResult(response);
      if (!response.ok || !result.username)
        throw Error(result.error || '登录失败，请重试。');
      if (!mounted.current) return;
      invalidateAuthRequests();
      setPassword('');
      setUser(result.username);
    } catch (e) {
      if (mounted.current)
        setError(e instanceof Error ? e.message : '登录失败，请重试。');
    } finally {
      mutationBusy.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function logout() {
    if (mutationBusy.current) return;
    mutationBusy.current = true;
    invalidateSessionCheck();
    setBusy(true);
    setError('');
    try {
      const response = await fetch(api + '/auth/logout', {
        method: 'POST',
        credentials: 'include',
      });
      await response.body?.cancel().catch(() => {});
      if (!response.ok) throw Error('退出失败，请重试。');
      if (!mounted.current) return;
      invalidateAuthRequests();
      setUser(null);
      setPassword('');
    } catch (e) {
      if (mounted.current)
        setError(e instanceof Error ? e.message : '退出失败，请重试。');
    } finally {
      mutationBusy.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  function renderPage(page: ReactNode) {
    if (!api && process.env.NODE_ENV === 'development') return page;
    if (checking)
      return (
        <div
          className="chip-site chip-workbench wb-site app-session-loading"
          aria-live="polite"
        >
          <SiteHeader />
          <WorkbenchPageBar title="量序" />
          <WorkbenchShell
            sidebar={
              <aside className="wb-sidebar">
                <WorkbenchListSkeleton />
              </aside>
            }
          >
            <WorkbenchState
              state={{ kind: 'loading', label: '正在验证登录…' }}
            />
          </WorkbenchShell>
        </div>
      );
    if (user)
      return (
        <AccountContext.Provider value={{ user, busy, logout, error }}>
          {page}
        </AccountContext.Provider>
      );
    return (
      <main className="auth-page">
        <AuthBackground />
        <AuthBrand />
        <section className="auth-form-panel" aria-labelledby="auth-title">
          <div className="auth-intro">
            <h1 id="auth-title">登录你的账户</h1>
          </div>
          {!api ? (
            <p role="alert" className="auth-error">
              登录服务未配置，请联系管理员。
            </p>
          ) : (
            <form onSubmit={login}>
              <label>
                账号
                <input
                  name="username"
                  autoComplete="username"
                  required
                  maxLength={64}
                  placeholder="输入你的账号"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
              <label>
                密码
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  maxLength={256}
                  placeholder="输入密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
              {error && (
                <p role="alert" className="auth-error">
                  {error}
                </p>
              )}
              {sessionCheckFailed && (
                <button
                  type="button"
                  className="auth-session-retry"
                  disabled={busy}
                  onClick={() => {
                    if (mutationBusy.current) return;
                    invalidateSessionCheck();
                    setError('');
                    setSessionCheckFailed(false);
                    setChecking(true);
                    setSessionRetry((value) => value + 1);
                  }}
                >
                  重新验证登录状态
                </button>
              )}
              <button type="submit" disabled={busy} aria-busy={busy}>
                {busy ? '登录中…' : '登录'}
                {busy ? (
                  <LoaderCircle
                    size={17}
                    className="animate-spin motion-reduce:animate-none"
                    aria-hidden="true"
                  />
                ) : (
                  <ArrowRight size={17} aria-hidden="true" />
                )}
              </button>
              <details className="auth-help">
                <summary>
                  <CircleHelp size={15} aria-hidden="true" />
                  账号帮助
                  <ChevronDown size={14} aria-hidden="true" />
                </summary>
                <p>账号由管理员提供。忘记密码或需要访问权限，请联系管理员。</p>
              </details>
            </form>
          )}
        </section>
      </main>
    );
  }
  return <SiteRouter renderPage={renderPage}>{children}</SiteRouter>;
}
