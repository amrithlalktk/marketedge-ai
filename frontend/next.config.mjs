/** @type {import('next').NextConfig} */
// API_URL: Docker / local setups (Next proxies /api/v1 to it). On Vercel the root vercel.json routes /api/v1/* to the
// backend service before Next.js is reached, so this rewrite is only a fallback there (BACKEND_URL from the binding).
const API_URL = (process.env.API_URL || process.env.BACKEND_URL || "http://localhost:8000").replace(/\/$/, "");

const isDev = process.env.NODE_ENV !== "production";
// Next.js injects inline bootstrap scripts, so script-src needs 'unsafe-inline' without a nonce setup;
// everything else is locked to same-origin (the API is proxied under /api/v1).
const CSP = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self'${isDev ? " ws: http://localhost:*" : ""}`,
  "worker-src 'self'",
  "manifest-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  ...(isDev ? [] : ["upgrade-insecure-requests"]),
].join("; ");

const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  experimental: {
    // Admin ingest/scan and backtests may run synchronously on the dev backend (eager jobs).
    proxyTimeout: 300_000,
  },
  async rewrites() {
    // Browser talks same-origin /api/v1/* -> FastAPI. Keeps the SameSite=Strict refresh cookie working and avoids CORS.
    return [{ source: "/api/v1/:path*", destination: `${API_URL}/api/v1/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          { key: "Content-Security-Policy", value: CSP },
          ...(isDev ? [] : [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]),
        ],
      },
    ];
  },
};

export default nextConfig;
