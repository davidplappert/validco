import { expect, test, type ConsoleMessage, type Page } from "@playwright/test";
import { stubApi } from "./fixtures";

/**
 * Content-Security-Policy regression tests.
 *
 * These exist because of a bug that reached production: the tile host was
 * listed under `img-src` but not `connect-src`, and MapLibre fetches raster
 * tiles through the Fetch API rather than as `<img>` elements. The result was a
 * blank map and several hundred CSP violations in the console — while every
 * unit test, every component test and the stubbed end-to-end suite passed,
 * because none of them ran a real CSP.
 *
 * The lesson generalises: a policy that is only asserted against the
 * CloudFormation template is not asserted against a browser. So these tests
 * apply the *real* deployed policy to the *real* bundle and fail on any
 * violation.
 */

/** Collect CSP violations and page errors while a block runs. */
async function watchForViolations(page: Page): Promise<{ violations: string[]; errors: string[] }> {
  const violations: string[] = [];
  const errors: string[] = [];

  page.on("console", (message: ConsoleMessage) => {
    const text = message.text();
    if (/Content Security Policy|Refused to (connect|load|execute)/i.test(text)) {
      violations.push(text);
    }
  });
  page.on("pageerror", (error) => errors.push(error.message));

  // The browser's own violation reporting, which catches cases the console
  // formats differently across engines.
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (event) => {
      // eslint-disable-next-line no-console
      console.error(
        `Content Security Policy violation: ${event.violatedDirective} blocked ${event.blockedURI}`,
      );
    });
  });

  return { violations, errors };
}

/**
 * The policy actually served in production, applied to the local bundle.
 *
 * Kept in sync with `infra/validco_infra/stack.py` — `test_security.py` asserts
 * the deployed template, and this asserts the browser honours it. If the two
 * drift, the stubbed API host below stops matching and these tests fail loudly.
 */
const DEPLOYED_CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https://tile.openstreetmap.org",
  "worker-src 'self' blob:",
  "connect-src 'self' https://tile.openstreetmap.org https://api.e2e.test",
  "font-src 'self' data:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

/** Serve every document with the real CSP header. */
async function applyCsp(page: Page) {
  await page.route("**/*", async (route) => {
    const request = route.request();
    if (request.resourceType() !== "document") return route.fallback();
    const response = await route.fetch();
    const body = await response.text();
    await route.fulfill({
      response,
      body,
      headers: {
        ...response.headers(),
        "content-security-policy": DEPLOYED_CSP,
      },
    });
  });
}

test.describe("under the production Content-Security-Policy", () => {
  test.beforeEach(async ({ page }) => {
    await applyCsp(page);
    await stubApi(page);
  });

  test("the app loads with no CSP violations", async ({ page }) => {
    const { violations, errors } = await watchForViolations(page);

    await page.goto("/");
    await expect(page.getByRole("heading", { name: /StepWise/i })).toBeVisible();
    // Give MapLibre time to initialise and request its first tiles.
    await page.waitForTimeout(2500);

    expect(violations, `CSP violations:\n${violations.slice(0, 5).join("\n")}`).toEqual([]);
    expect(errors).toEqual([]);
  });

  test("the map fetches basemap tiles successfully", async ({ page }) => {
    // The specific failure: MapLibre uses fetch() for raster tiles, so the tile
    // host must be in connect-src. Asserting the requests *succeed* catches it
    // where asserting the canvas exists did not.
    const tileStatuses: number[] = [];
    page.on("response", (response) => {
      if (response.url().includes("tile.openstreetmap.org")) {
        tileStatuses.push(response.status());
      }
    });
    const { violations } = await watchForViolations(page);

    await page.goto("/");
    await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible();
    await page.waitForTimeout(2500);

    expect(violations).toEqual([]);
    expect(tileStatuses.length, "the map should request basemap tiles").toBeGreaterThan(0);
    expect(
      tileStatuses.every((status) => status === 200),
      `tile responses: ${tileStatuses.slice(0, 10).join(", ")}`,
    ).toBe(true);
  });

  test("planning a walk makes no blocked requests", async ({ page }) => {
    const { violations, errors } = await watchForViolations(page);

    await page.goto("/");
    await page.getByLabel(/Start address/i).fill("100 N Main St, Morton, IL");
    await page.getByRole("button", { name: /find me a walk/i }).click();
    await expect(page.getByLabel("Suggested walks")).toBeVisible();
    await page.waitForTimeout(1500);

    expect(violations, `CSP violations:\n${violations.slice(0, 5).join("\n")}`).toEqual([]);
    expect(errors).toEqual([]);
  });

  test("the API host is reachable under the policy", async ({ page }) => {
    // connect-src must permit the execute-api domain, or every request fails.
    const apiStatuses: number[] = [];
    page.on("response", (response) => {
      if (response.url().includes("api.e2e.test")) apiStatuses.push(response.status());
    });

    await page.goto("/");
    await page.getByLabel(/Start address/i).fill("100 N Main St");
    await page.getByRole("button", { name: /find me a walk/i }).click();
    await expect(page.getByLabel("Suggested walks")).toBeVisible();

    expect(apiStatuses.length).toBeGreaterThan(0);
    expect(apiStatuses.every((status) => status === 200)).toBe(true);
  });
});
