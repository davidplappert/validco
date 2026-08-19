import { expect, test } from "@playwright/test";

/**
 * Smoke tests against a **real deployment**.
 *
 * Everything else in this directory stubs the API so it can run fast and
 * deterministically in CI. These do the opposite: nothing is intercepted, so
 * they exercise the real CloudFront distribution, the real config.json written
 * by CDK, the real API Gateway stage and the real Lambda — including its cold
 * start.
 *
 * They are skipped unless `E2E_BASE_URL` is set, which the deploy workflow does
 * after `cdk deploy` succeeds.
 */

const DEPLOYED = Boolean(process.env.E2E_BASE_URL);

test.skip(!DEPLOYED, "set E2E_BASE_URL to run against a deployment");

// A cold Lambda decoding several megabytes of graph arrays is slower than a
// warm one, and this may well be the first request the container ever sees.
test.setTimeout(90_000);

test("serves the app over HTTPS from CloudFront", async ({ page }) => {
  const response = await page.goto("/");
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: /StepWise/i })).toBeVisible();
});

test("config.json carries a usable API URL", async ({ request }) => {
  const response = await request.get("/config.json");
  expect(response.status()).toBe(200);

  const config = await response.json();
  expect(config.apiBaseUrl).toMatch(/^https:\/\//);

  // The URL in config.json must actually answer, or the app is broken for
  // everyone even though the deploy went green.
  const health = await request.get(`${config.apiBaseUrl}/v1/health`);
  expect(health.status()).toBe(200);
  expect((await health.json()).ok).toBe(true);
});

test("plans a real walk end to end", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("100 N Main St, Chillicothe, IL 61523");
  await page.getByRole("button", { name: /find me a walk/i }).click();

  const list = page.getByLabel("Suggested walks");
  await expect(list).toBeVisible({ timeout: 60_000 });
  await expect(list.getByRole("button").first()).toBeVisible();

  // The origin summary proves the geocoder resolved against the real Overture
  // address corpus rather than a fixture.
  await expect(page.getByText(/Main Street/i)).toBeVisible();
  await expect(page.getByText(/Snapped \d+ m to the walking network/)).toBeVisible();
});

test("plans a walk in San Francisco too", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("1000 California St, San Francisco");
  await page.getByRole("button", { name: /find me a walk/i }).click();
  await expect(page.getByLabel("Suggested walks")).toBeVisible({ timeout: 60_000 });
});

test("reports a genuinely uncovered address rather than failing", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("10 Downing Street, London SW1A 2AA");
  await page.getByRole("button", { name: /find me a walk/i }).click();
  await expect(page.getByRole("alert", { name: "Planning error" })).toBeVisible({
    timeout: 60_000,
  });
});
