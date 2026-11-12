import type { NextConfig } from "next";

/**
 * Content-Security-Policy for the app's pages (production builds; the dev server needs eval
 * and a websocket for hot reload). Everything is same-origin: no image, font, script, media or
 * fetch from another host, so text a model wrote (an answer quoting data) cannot make the
 * browser send anything elsewhere. Next.js inlines its bootstrap scripts, hence 'unsafe-inline'.
 */
export const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "media-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'self'",
].join("; ");

const nextConfig: NextConfig = {
  // Self-contained server bundle for the Docker image (see Dockerfile).
  output: "standalone",
  poweredByHeader: false,
  experimental: {
    // The dev rewrite below proxies /api through Next.js, which keeps only the first 10 MB of a
    // request body by default: a meeting recording (up to 500 MB, MAX_MEETING_BYTES) arrived
    // truncated and failed. Caddy (Docker) never sends /api through Next.js.
    proxyClientMaxBodySize: "500mb",
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  /**
   * In Docker, Caddy serves the web app and the API on one origin, so `/api/*`
   * never reaches Next.js. For local `pnpm dev` (port 3400) we proxy it to the API.
   * Production builds get no rewrites: next.config is serialized at build time.
   */
  // The assistant moved to /chat (Phase 4); "Workflows" became Approvals and Tasks, with
  // workflow definitions under Admin → Workflows (Phase 8).
  async headers() {
    if (process.env.NODE_ENV !== "production") return [];
    return [{ source: "/:path*", headers: [{ key: "Content-Security-Policy", value: CONTENT_SECURITY_POLICY }] }];
  },
  async redirects() {
    return [
      { source: "/assistant", destination: "/chat", permanent: false },
      { source: "/workflows", destination: "/approvals", permanent: false },
      // Notifications stored before workflow links moved to Admin › Workflows (work/links.py).
      { source: "/workflows/:key", destination: "/admin/workflows?workflow=:key", permanent: false },
    ];
  },
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    const apiInternalUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8400";
    return [{ source: "/api/:path*", destination: `${apiInternalUrl}/api/:path*` }];
  },
};

export default nextConfig;
