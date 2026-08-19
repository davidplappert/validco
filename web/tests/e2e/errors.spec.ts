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
 * Failure paths. An app that only works when everything succeeds is not
 * finished, and the geocoder missing is the single most likely failure here.
 */

test("a geocoding miss offers street suggestions", async ({ page }) => {
  await stubApi(page, 404, {
    error: "could not find 'Nowhere Street' in any covered region",
    suggestions: { sf: ["Noe Street", "Nob Hill Circle"], pia: [] },
  });
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("Nowhere Street");
  await page.getByRole("button", { name: /find me a walk/i }).click();

  const alert = page.getByRole("alert", { name: "Planning error" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("could not find");
  await expect(alert).toContainText("Did you mean: Noe Street, Nob Hill Circle?");
  // The covered areas are named, because "outside coverage" is the usual cause.
  await expect(alert).toContainText("San Francisco");
  await expect(alert).toContainText("Peoria");
});

test("a point outside coverage explains itself", async ({ page }) => {
  await stubApi(page, 422, {
    error: "those coordinates are outside every covered region",
    regions: ["sf", "pia"],
  });
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("10 Downing Street, London");
  await page.getByRole("button", { name: /find me a walk/i }).click();
  await expect(page.getByRole("alert", { name: "Planning error" })).toContainText("outside every covered region");
});

test("a server error does not leave the button stuck", async ({ page }) => {
  await stubApi(page, 500, { error: "internal error", request_id: "abc-123" });
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("Market St");
  await page.getByRole("button", { name: /find me a walk/i }).click();

  await expect(page.getByRole("alert", { name: "Planning error" })).toContainText("internal error");
  await expect(page.getByRole("button", { name: /find me a walk/i })).toBeEnabled();
});

test("a successful retry clears the previous error", async ({ page }) => {
  await stubApi(page, 500, { error: "internal error" });
  await page.goto("/");
  await page.getByLabel(/Start address/i).fill("Market St");
  await page.getByRole("button", { name: /find me a walk/i }).click();
  await expect(page.getByRole("alert", { name: "Planning error" })).toBeVisible();

  await stubApi(page);
  await page.getByRole("button", { name: /find me a walk/i }).click();
  await expect(page.getByRole("alert", { name: "Planning error" })).toHaveCount(0);
  await expect(page.getByLabel("Suggested walks")).toBeVisible();
});
