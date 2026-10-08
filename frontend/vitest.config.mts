import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    // Unit tests only. The end-to-end tests in e2e/ run with Playwright (npm run e2e).
    include: ["tests/**/*.test.ts"],
    environment: "node",
  },
});
