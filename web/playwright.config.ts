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

/**
 * The screen sizes the layout claims to support.
 *
 * Each becomes its own project so `responsive.spec.ts` can be written once,
 * viewport-agnostically, and be re-run at every size. They are restricted to
 * that one spec: running the whole suite nine times over would cost minutes to
 * re-prove things that do not depend on the viewport at all.
 */
const RESPONSIVE_VIEWPORTS: { name: string; width: number; height: number }[] = [
  { name: "phone-small", width: 320, height: 568 },
  { name: "phone", width: 390, height: 844 },
  { name: "tablet-portrait", width: 768, height: 1024 },
  { name: "tablet-landscape", width: 1024, height: 768 },
  { name: "laptop", width: 1440, height: 900 },
  { name: "desktop", width: 1920, height: 1080 },
  { name: "desktop-wide", width: 2560, height: 1440 },
];

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
    // `chromium` and `mobile` run the full suite and are the two names the CI
    // workflow's matrix refers to. Do not rename them without changing it.
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
    ...RESPONSIVE_VIEWPORTS.map(({ name, width, height }) => ({
      name,
      testMatch: /responsive\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width, height } },
    })),
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
