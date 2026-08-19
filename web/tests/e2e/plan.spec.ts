import { expect, test } from "@playwright/test";

// These stub the network, so they describe the bundle's behaviour rather than a
// deployment's. Running them against E2E_BASE_URL would intercept the real API
// and prove nothing; `deployed.spec.ts` covers that case instead.
test.skip(
  Boolean(process.env.E2E_BASE_URL),
  "stubbed specs run against the local export only",
);
import { API_HOST, PLAN_RESPONSE, stubApi } from "./fixtures";

/**
 * The full journey through the real bundle: fill the form, get suggestions,
 * expand one, see it drawn on the map.
 */

test.beforeEach(async ({ page }) => {
  await stubApi(page);
});

test("loads with the form ready and no results", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /StepWise/i })).toBeVisible();
  await expect(page.getByLabel(/Start address/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /find me a walk/i })).toBeEnabled();
  await expect(page.getByLabel("Suggested walks")).toHaveCount(0);
});

test("plans a walk and shows ranked suggestions", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("100 N Main St, Morton, IL");

  const request = page.waitForRequest(`${API_HOST}/v1/plan`);
  await page.getByRole("button", { name: /find me a walk/i }).click();

  // The request must carry the profile the form collected.
  const payload = JSON.parse((await request).postData() ?? "{}");
  expect(payload.address).toBe("100 N Main St, Morton, IL");
  expect(payload.profile.sex).toBe("male");
  expect(payload.minutes).toBe(30);

  const list = page.getByLabel("Suggested walks");
  await expect(list).toBeVisible();
  await expect(list.getByRole("button")).toHaveCount(PLAN_RESPONSE.routes.length);

  // The origin summary echoes what the API derived.
  await expect(page.getByText("100 North MAIN Street, 61550")).toBeVisible();
  await expect(page.getByText(/BMI 49 \(obesity class III\)/)).toBeVisible();
  await expect(page.getByText(/Snapped 42 m to the walking network/)).toBeVisible();
});

test("expands a route to reveal its health detail", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("100 N Main St");
  await page.getByRole("button", { name: /find me a walk/i }).click();

  const cards = page.getByLabel("Suggested walks").getByRole("button");
  await expect(cards.first()).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText(/Weekly activity target/i)).toBeVisible();
  await expect(page.getByText(/Daily steps/i)).toBeVisible();

  // Selecting the second card moves the expansion.
  await cards.nth(1).click();
  await expect(cards.nth(1)).toHaveAttribute("aria-expanded", "true");
  await expect(cards.first()).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText("Out to Riverfront Park")).toBeVisible();
});

test("renders the route on the map canvas", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("100 N Main St");
  await page.getByRole("button", { name: /find me a walk/i }).click();
  await expect(page.getByLabel("Suggested walks")).toBeVisible();

  // MapLibre draws into a WebGL canvas, so the assertion is that the canvas
  // exists and has been sized — pixel comparison would be flaky across runners.
  const canvas = page.locator("canvas.maplibregl-canvas");
  await expect(canvas).toBeVisible();
  const box = await canvas.boundingBox();
  expect(box?.width ?? 0).toBeGreaterThan(100);
});

test("toggling a preference changes what is requested", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("100 N Main St");
  await page.getByRole("button", { name: "Avoid hills" }).click();

  const request = page.waitForRequest(`${API_HOST}/v1/plan`);
  await page.getByRole("button", { name: /find me a walk/i }).click();
  const payload = JSON.parse((await request).postData() ?? "{}");
  expect(payload.preferences.avoid_hills).toBe(true);
});

test("shows the data attribution", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/Overture Maps Foundation/)).toBeVisible();
  await expect(page.getByText(/not medical advice/i)).toBeVisible();
});
