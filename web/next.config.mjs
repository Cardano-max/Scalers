/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The console is a self-hosted internal app served behind the Cloudflare tunnel.
  // No image optimization service is configured locally; all iconography is inline SVG.
  images: { unoptimized: true },
};

export default nextConfig;
