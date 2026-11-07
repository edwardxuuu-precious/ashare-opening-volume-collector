import type { Metadata } from 'next';
import './globals.css';
import { AuthGate } from '@/components/auth-gate';
import { BRAND_NAME } from '@/components/brand';
import { siteHistoryBootstrap } from '@/lib/site-routes';

export const metadata: Metadata = {
  title: `${BRAND_NAME} · A股数据`,
  description: '查询A股首15分钟成交量占全天比例与日行情。',
  icons: { icon: '/favicon.svg' },
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  let authOrigin: string | undefined;
  try {
    const url = new URL(process.env.NEXT_PUBLIC_AUTH_API || '');
    if (url.protocol === 'http:' || url.protocol === 'https:')
      authOrigin = url.origin;
  } catch {}
  return (
    <html lang="zh-CN">
      <head>
        {authOrigin && (
          <>
            <link
              rel="preconnect"
              href={authOrigin}
              crossOrigin="use-credentials"
            />
            <link rel="dns-prefetch" href={authOrigin} />
          </>
        )}
        <script dangerouslySetInnerHTML={{ __html: siteHistoryBootstrap }} />
      </head>
      <body className="antialiased">
        <AuthGate>{children}</AuthGate>
      </body>
    </html>
  );
}
