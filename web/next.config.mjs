/** @type {import('next').NextConfig} */

// The browser reaches the engine's AG-UI + GraphQL + SSE surface same-origin via
// Next rewrites: the page calls `/studio/agui`, `/graphql`, `/sse/*` on the web
// origin and Next streams them to the engine. This avoids CORS and works on any
// port. Default target is the integrated go-live engine on :8010 (the obsapi
// GraphQL + /sse/stream + /sse/feed live here). Override with STUDIO_BACKEND_ORIGIN.
const STUDIO_BACKEND_ORIGIN = process.env.STUDIO_BACKEND_ORIGIN || 'http://127.0.0.1:8010';

const nextConfig = {
  reactStrictMode: true,
  // The console is a self-hosted internal app served behind the Cloudflare tunnel.
  // No image optimization service is configured locally; all iconography is inline SVG.
  images: { unoptimized: true },
  async rewrites() {
    return [
      { source: '/studio/:path*', destination: `${STUDIO_BACKEND_ORIGIN}/studio/:path*` },
      { source: '/graphql', destination: `${STUDIO_BACKEND_ORIGIN}/graphql` },
      // SSE realtime (Live feed / Overview): /sse/stream + /sse/feed. Proxied
      // same-origin so EventSource never trips CORS and survives any dev port.
      { source: '/sse/:path*', destination: `${STUDIO_BACKEND_ORIGIN}/sse/:path*` },
    ];
  },
};

export default nextConfig;
