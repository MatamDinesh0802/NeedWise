import type { NextConfig } from "next";

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
  async redirects() {
    return [
      { source: "/assistant", destination: "/chat", permanent: false },
      { source: "/workflows", destination: "/approvals", permanent: false },
    ];
  },
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    const apiInternalUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8400";
    return [{ source: "/api/:path*", destination: `${apiInternalUrl}/api/:path*` }];
  },
};

export default nextConfig;
