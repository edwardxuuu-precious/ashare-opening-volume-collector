'use client';

import {
  createContext,
  useContext,
  useEffect,
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
  UserRound,
} from 'lucide-react';
import { readResponse } from '../lib/read-request.ts';
import { BrandIdentity } from './brand';

const AccountContext = createContext<{
  user: string;
  busy: boolean;
  logout: () => void;
  error: string;
} | null>(null);

export function AccountControls() {
  const account = useContext(AccountContext);
  if (!account) return null;
  return (
    <div className="account-controls">
      <UserRound size={16} aria-hidden="true" />
      <span>{account.user}</span>
      <button
        type="button"
        disabled={account.busy}
        onClick={account.logout}
        aria-label="退出登录"
        title="退出登录"
      >
        <LogOut size={16} aria-hidden="true" />
      </button>
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
      'username' in value && typeof value.username === 'string'
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
  useEffect(() => {
    if (!api) return;
    const controller = new AbortController();
    const expired = () => {
      setUser(null);
      setError('登录已过期，请重新登录。');
    };
    window.addEventListener('stock-session-expired', expired);
    readResponse(
      api + '/auth/me',
      { credentials: 'include', signal: controller.signal, cache: 'no-store' },
      async (r) => {
        if (r.status === 401) return null;
        if (!r.ok)
          throw Error(`登录状态读取失败，请重试。（HTTP ${r.status}）`);
        return readAuthResult(r);
      },
    )
      .then((result) => {
        if (!controller.signal.aborted && result?.username)
          setUser(result.username);
      })
      .catch((e) => {
        if (e.name !== 'AbortError')
          setError(
            e instanceof Error ? e.message : '无法连接登录服务，请稍后重试。',
          );
      })
      .finally(() => {
        if (!controller.signal.aborted) setChecking(false);
      });
    return () => {
      controller.abort();
      window.removeEventListener('stock-session-expired', expired);
    };
  }, []);
  // Local development remains usable without a cloud auth service. Production
  // packaging separately enforces a configured endpoint and strips all data.
  if (!api && process.env.NODE_ENV === 'development') return children;
  async function login(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
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
      setPassword('');
      setUser(result.username);
    } catch (e) {
      setError(e instanceof Error ? e.message : '登录失败，请重试。');
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    setBusy(true);
    setError('');
    try {
      const response = await fetch(api + '/auth/logout', {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) throw Error('退出失败，请重试。');
      setUser(null);
      setPassword('');
    } catch (e) {
      setError(e instanceof Error ? e.message : '退出失败，请重试。');
    } finally {
      setBusy(false);
    }
  }
  if (checking)
    return (
      <main className="auth-page auth-check" aria-live="polite">
        <AuthBackground />
        <AuthBrand />
        <div className="auth-check-card">
          <LoaderCircle
            size={19}
            className="animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          验证登录…
        </div>
      </main>
    );
  if (user)
    return (
      <AccountContext.Provider value={{ user, busy, logout, error }}>
        {children}
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
