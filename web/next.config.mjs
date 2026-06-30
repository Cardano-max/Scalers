/** @type {import('next').NextConfig} */

// The studio dev FE (port 3002) reaches the engine's AG-UI + GraphQL surface on a
// different port (:8002). Rather than open CORS, we proxy them same-origin via Next
// rewrites: the browser calls `/studio/agui` and `/graphql` on :3002 and Next streams
// them to the engine. Override the target with STUDIO_BACKEND_ORIGIN.
const STUDIO_BACKEND_ORIGIN = process.env.STUDIO_BACKEND_ORIGIN || 'http://127.0.0.1:8002';

const nextConfig = {
  reactStrictMode: true,
  // The console is a self-hosted internal app served behind the Cloudflare tunnel.
  // No image optimization service is configured locally; all iconography is inline SVG.
  images: { unoptimized: true },
  async rewrites() {
    return [
      { source: '/studio/:path*', destination: `${STUDIO_BACKEND_ORIGIN}/studio/:path*` },
      { source: '/graphql', destination: `${STUDIO_BACKEND_ORIGIN}/graphql` },
    ];
  },
};

export default nextConfig;
