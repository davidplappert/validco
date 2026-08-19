import { expect, test } from "@playwright/test";

// These stub the network, so they describe the bundle's behaviour rather than a
// deployment's. Running them against E2E_BASE_URL would intercept the real API
// and prove nothing; `deployed.spec.ts` covers that case instead.
test.skip(Boolean(process.env.E2E_BASE_URL), "stubbed specs run against the local export only");
import { PLAN_RESPONSE, stubApi } from "./fixtures";
import { openApp, planAfter, planFor, planPayload, submitButton } from "./support/form";

/**
 * The full journey through the real bundle: fill the form, get suggestions,
 * expand one, see it drawn on the map.
 *
 * Note what is *absent* from most of these now. The app plans its default
 * address on load, so a spec that only needs results on screen no longer types
 * anything at all — `openApp` waits for the opening plan and that is the whole
 * setup. Typing survives only where the spec is about what was typed.
 */

test.beforeEach(async ({ page }) => {
  await stubApi(page);
});

test("opens with a plan already run, not an empty form", async ({ page }) => {
  // The app plans its default address on load. An empty first screen would ask
  // the user to do work before they can tell whether the product is any good,
  // so the opening state is a real route with real numbers.
  await openApp(page);
  await expect(page.getByRole("heading", { name: /StepWise/i })).toBeVisible();

  const address = page.getByLabel(/Start address/i);
  await expect(address).toBeVisible();
  await expect(address).not.toHaveValue("");

  await expect(page.getByLabel("Suggested walks")).toBeVisible();
  // Nobody has to press anything, and the form says so.
  await expect(page.getByText("Walks update as you type.")).toBeVisible();
});

test("the submit button is present for Enter and for assistive technology", async ({ page }) => {
  // It is `sr-only`, not absent. HTML only performs implicit submission when a
  // form has a submit button or exactly one field, so deleting it would break
  // Enter in every browser — and a form that acts on a timer owes a
  // screen-reader user a way to say "now".
  await openApp(page);

  const submit = submitButton(page);
  await expect(submit).toBeAttached();
  await expect(submit).toBeEnabled();

  // Hidden until it has focus, and plainly visible once it does.
  await submit.focus();
  await expect(submit).toBeVisible();
  await expect(submit).toBeFocused();
});

test("plans a walk and shows ranked suggestions", async ({ page }) => {
  await openApp(page);

  const request = await planFor(page, "100 N Main St, Morton, IL");

  // The request must carry the profile the form collected.
  const payload = planPayload(request);
  expect(payload.address).toBe("100 N Main St, Morton, IL");
  expect(payload.profile?.sex).toBe("male");
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
  await openApp(page);

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
  await openApp(page);
  await expect(page.getByLabel("Suggested walks")).toBeVisible();

  // MapLibre draws into a WebGL canvas, so the assertion is that the canvas
  // exists and has been sized — pixel comparison would be flaky across runners.
  const canvas = page.locator("canvas.maplibregl-canvas");
  await expect(canvas).toBeVisible();
  const box = await canvas.boundingBox();
  expect(box?.width ?? 0).toBeGreaterThan(100);
});

test("toggling a preference changes what is requested", async ({ page }) => {
  await openApp(page);

  // A chip is an "adjusting" change rather than free text, so it waits out the
  // shorter delay — and then plans without anyone pressing anything.
  const request = await planAfter(page, () =>
    page.getByRole("button", { name: "Avoid hills" }).click(),
  );
  expect(planPayload(request).preferences?.avoid_hills).toBe(true);
});

test("shows the data attribution", async ({ page }) => {
  await openApp(page);
  // Scoped to the footer: the medical disclaimer also appears in each route's
  // caveat list once results are on screen, and an unscoped match resolves to
  // several elements.
  const footer = page.locator("footer");
  await expect(footer).toContainText(/Overture Maps Foundation/);
  await expect(footer).toContainText(/OpenStreetMap/);
  await expect(footer).toContainText(/not medical advice/i);
});
