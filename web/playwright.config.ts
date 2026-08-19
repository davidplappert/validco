import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests.
 *
 * These run against the built static export with a stubbed API, so they
 * exercise the real MapLibre canvas and the real bundle without depending on a
 * deployed backend — which keeps them fast and deterministic in CI.
 *
 * Set `E2E_BASE_URL` to point them at a real deployment instead; the smoke
 * suite in the deploy workflow does exactly that.
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:4173";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],
  timeout: 30_000,
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  // Only start a server for the local static export; when E2E_BASE_URL points
  // at a deployment there is nothing to run.
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: "npx serve out -l 4173 --no-clipboard",
        url: "http://127.0.0.1:4173",
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
});
