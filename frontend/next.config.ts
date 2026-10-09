import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

/**
 * Content-Security-Policy. Next.js puts its start-up scripts inline in every page. Allowing
 * them by nonce would make every page render on the server, and nonces do not work with
 * Cache Components, so scripts need 'unsafe-inline' (React needs 'unsafe-eval' in dev
 * only). The rest still protects a lot: the page talks only to its own server (injected
 * code cannot send data elsewhere), loads nothing from other sites, and is never framed.
 */
export const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  // A small self-contained server (.next/standalone) for the Docker image.
  output: "standalone",
  poweredByHeader: false,
  // Pages render in the browser and fetch their data through /api/v1 (the login token
  // never reaches JavaScript), so these Next.js 16 defaults cost nothing here.
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
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: CONTENT_SECURITY_POLICY },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
