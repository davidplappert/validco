import { expect, test } from "@playwright/test";

// These stub the network, so they describe the bundle's behaviour rather than a
// deployment's. Running them against E2E_BASE_URL would intercept the real API
// and prove nothing; `deployed.spec.ts` covers that case instead.
test.skip(Boolean(process.env.E2E_BASE_URL), "stubbed specs run against the local export only");
import { stubApi } from "./fixtures";
import { openApp, planFor, planNow, settle, submitButton } from "./support/form";

/**
 * Failure paths. An app that only works when everything succeeds is not
 * finished, and the geocoder missing is the single most likely failure here.
 *
 * A form that plans as you type fails *far* more often than one with a button:
 * every half-finished address is a request that cannot geocode. That is why the
 * last test here asserts the results stay on screen through a failure — see
 * `usePlanner`.
 */

test("a geocoding miss offers street suggestions", async ({ page }) => {
  await stubApi(page, 404, {
    error: "could not find 'Nowhere Street' in any covered region",
    suggestions: { sf: ["Noe Street", "Nob Hill Circle"], pia: [] },
  });
  await openApp(page);
  await planFor(page, "Nowhere Street");

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
  await openApp(page);
  await planFor(page, "10 Downing Street, London");
  await expect(page.getByRole("alert", { name: "Planning error" })).toContainText(
    "outside every covered region",
  );
});

test("a server error does not leave the form stuck", async ({ page }) => {
  await stubApi(page, 500, { error: "internal error", request_id: "abc-123" });
  await openApp(page);
  await planFor(page, "Market St");

  await expect(page.getByRole("alert", { name: "Planning error" })).toContainText("internal error");
  // Both ways of asking for another attempt are live again: the form says it is
  // listening, and the submit button is no longer disabled.
  await settle(page);
  await expect(submitButton(page)).toBeEnabled();
});

test("a successful retry clears the previous error", async ({ page }) => {
  await stubApi(page, 500, { error: "internal error" });
  await openApp(page);
  await planFor(page, "Market St");
  await expect(page.getByRole("alert", { name: "Planning error" })).toBeVisible();

  // Nothing about the form changes here, so there is no debounce to wait out —
  // this is exactly what the hidden submit button is for.
  await stubApi(page);
  await planNow(page);
  await expect(page.getByRole("alert", { name: "Planning error" })).toHaveCount(0);
  await expect(page.getByLabel("Suggested walks")).toBeVisible();
});

test("a failed plan leaves the previous walks on screen", async ({ page }) => {
  // Deliberate, and the opposite of what a submit-button form should do. While
  // the form plans as you type most failures are transient — a half-typed
  // address that does not geocode yet — and blanking the map on each one makes
  // a working app flicker between working and broken. The error is shown above
  // a result that is merely stale, which is the more honest of the two states.
  await stubApi(page);
  await openApp(page);
  const results = page.getByLabel("Suggested walks");
  await expect(results).toBeVisible();

  await stubApi(page, 500, { error: "internal error" });
  await planFor(page, "Market St");

  await expect(page.getByRole("alert", { name: "Planning error" })).toBeVisible();
  await expect(results).toBeVisible();
  await expect(page.getByText("100 North MAIN Street, 61550")).toBeVisible();
});
