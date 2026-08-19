import { expect, test } from "@playwright/test";

// These stub the network, so they describe the bundle's behaviour rather than a
// deployment's. Running them against E2E_BASE_URL would intercept the real API
// and prove nothing; `deployed.spec.ts` covers that case instead.
test.skip(
  Boolean(process.env.E2E_BASE_URL),
  "stubbed specs run against the local export only",
);
import { stubApi } from "./fixtures";

/**
 * Keyboard and screen-reader affordances.
 *
 * The route cards carry state that is only otherwise conveyed by colour and
 * position, so their ARIA attributes are the accessible interface, not a nicety.
 */

test.beforeEach(async ({ page }) => {
  await stubApi(page);
});

test("the form is reachable and submittable by keyboard alone", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Start address/i).focus();
  await page.keyboard.type("100 N Main St");
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Suggested walks")).toBeVisible();
});

test("route cards expose their selected state", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("100 N Main St");
  await page.getByRole("button", { name: /find me a walk/i }).click();

  const cards = page.getByLabel("Suggested walks").getByRole("button");
  await expect(cards.first()).toHaveAttribute("aria-pressed", "true");
  await expect(cards.nth(1)).toHaveAttribute("aria-pressed", "false");
});

test("progress bars report their value to assistive technology", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("100 N Main St");
  await page.getByRole("button", { name: /find me a walk/i }).click();

  const bars = page.getByRole("progressbar");
  await expect(bars.first()).toHaveAttribute("aria-valuenow", /\d+/);
});

test("the elevation chart has a text alternative", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("100 N Main St");
  await page.getByRole("button", { name: /find me a walk/i }).click();
  await expect(page.getByRole("img", { name: /Elevation profile/i })).toBeVisible();
});

test("the page has exactly one h1", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("h1")).toHaveCount(1);
});
