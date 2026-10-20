import type { NextConfig } from 'next';
const nextConfig: NextConfig = {
  output: 'export',
  // Vinext beta.5 redirects /chips during prerender when trailingSlash is true.
  // The existing root URL is unchanged; named static routes export as *.html.
  trailingSlash: false,
  basePath: process.env.SITE_BASE_PATH || '',
};
export default nextConfig;
