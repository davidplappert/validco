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
  await page
    .getByLabel(/Start address/i)
    .fill("100 N Main St, Morton, IL 61550");
  await page.getByRole("button", { name: /find me a walk/i }).click();

  const list = page.getByLabel("Suggested walks");
  await expect(list).toBeVisible({ timeout: 60_000 });
  await expect(list.getByRole("button").first()).toBeVisible();

  // The origin summary proves the geocoder resolved against the real Overture
  // address corpus rather than a fixture.
  //
  // Matched as a whole line rather than a substring: the street name also
  // appears in each route's "via ..." list, and a loose regex matches several
  // elements at once, which Playwright treats as an error rather than a pass.
  await expect(page.getByText(/^\d+ North MAIN Street, \d{5}$/i)).toBeVisible();
  await expect(
    page.getByText(/Snapped \d+ m to the walking network/),
  ).toBeVisible();
});

test("plans a walk in San Francisco too", async ({ page }) => {
  await page.goto("/");
  await page
    .getByLabel(/Start address/i)
    .fill("1000 California St, San Francisco");
  await page.getByRole("button", { name: /find me a walk/i }).click();
  await expect(page.getByLabel("Suggested walks")).toBeVisible({
    timeout: 60_000,
  });
});

test("offers to build an uncovered area rather than refusing it", async ({
  page,
}) => {
  // This used to assert a planning error, and the change is the point: an
  // address outside the two bundled cities is no longer a dead end, it is an
  // offer to extract that area from Overture. The test is deliberately left
  // at the offer and does not accept it — a real build writes to the shared
  // region bucket, and a deploy check should not mutate production data or
  // wait two minutes to prove a button works.
  //
  // It also doubles as a configuration check: the offer only appears when the
  // catalogue is enabled, which requires REGION_BUCKET in the Lambda's
  // environment. Deploy that without it and this test fails — which is how the
  // missing variable should have been caught the first time.
  await page.goto("/");
  await page
    .getByLabel(/Start address/i)
    .fill("10 Downing Street, London SW1A 2AA");
  await page.getByRole("button", { name: /find me a walk/i }).click();

  const offer = page.getByRole("alert", { name: "Coverage needed" });
  await expect(offer).toBeVisible({ timeout: 60_000 });
  await expect(offer.getByRole("button")).toBeEnabled();
});
