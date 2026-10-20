import type { Metadata } from 'next';
import './globals.css';
import { AuthGate } from '@/components/auth-gate';
import { BRAND_NAME } from '@/components/brand';

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
  return (
    <html lang="zh-CN">
      <body className="antialiased">
        <AuthGate>{children}</AuthGate>
      </body>
    </html>
  );
}
