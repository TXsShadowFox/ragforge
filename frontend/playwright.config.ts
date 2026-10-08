import { defineConfig } from "@playwright/test";

/**
 * The whole flow in a real browser, against the running stack (`make up`) and the real
 * LLM. On Windows it uses Edge, which is already installed; elsewhere (or with
 * E2E_BROWSER=chromium) it uses Playwright's Chromium: `npx playwright install chromium`.
 */
const useEdge = process.platform === "win32" && process.env.E2E_BROWSER !== "chromium";

export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.DASHBOARD_URL ?? "http://localhost:3000",
    channel: useEdge ? "msedge" : undefined,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    // The "customer website" with the widget: another origin than the dashboard and the API.
    command: "uv run python -m http.server 5500 --directory ../widget",
    url: "http://localhost:5500/demo.html",
    reuseExistingServer: true,
  },
});
