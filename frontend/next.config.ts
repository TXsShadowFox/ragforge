import type { NextConfig } from "next";

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
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
