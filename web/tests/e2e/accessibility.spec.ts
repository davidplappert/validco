import { expect, test } from "@playwright/test";

// These stub the network, so they describe the bundle's behaviour rather than a
// deployment's. Running them against E2E_BASE_URL would intercept the real API
// and prove nothing; `deployed.spec.ts` covers that case instead.
test.skip(Boolean(process.env.E2E_BASE_URL), "stubbed specs run against the local export only");
import { stubApi } from "./fixtures";
import { addressField, openApp, planNow, settle, submitButton } from "./support/form";

/**
 * Keyboard and screen-reader affordances.
 *
 * The route cards carry state that is only otherwise conveyed by colour and
 * position, so their ARIA attributes are the accessible interface, not a nicety.
 *
 * Two of these exist because the form submits itself now. A form that acts on a
 * timer has to keep a way for somebody to say "now" — an assistive-technology
 * user cannot "stop typing and wait" as an interaction — and it has to keep
 * announcing what it is doing, or the map redrawing itself is unexplained.
 */

test.beforeEach(async ({ page }) => {
  await stubApi(page);
});

test("the form is reachable and submittable by keyboard alone", async ({ page }) => {
  await openApp(page);
  await addressField(page).focus();
  await page.keyboard.type("100 N Main St");
  // Enter submits without waiting out the debounce — implicit submission via
  // the sr-only button, which is why that button may not be deleted.
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Suggested walks")).toBeVisible();
  await settle(page);
});

test("the timer-driven form still offers an explicit way to say now", async ({ page }) => {
  await openApp(page);

  // Reachable by role and name even while it is visually hidden, which is what
  // a screen reader and a switch device see.
  const submit = submitButton(page);
  await expect(submit).toBeEnabled();
  await planNow(page);
  await expect(page.getByLabel("Suggested walks")).toBeVisible();
});

test("the form says what it is doing, politely", async ({ page }) => {
  await openApp(page);
  // `aria-live="polite"`, not an alert: an update in progress is information,
  // and this page already has one assertive region (`ErrorPanel`).
  const status = page.getByText("Walks update as you type.");
  await expect(status).toBeVisible();
  await expect(status).toHaveAttribute("aria-live", "polite");
});

test("the address field exposes itself as a combobox", async ({ page }) => {
  await openApp(page);
  const field = addressField(page);

  await expect(field).toHaveRole("combobox");
  await expect(field).toHaveAttribute("aria-autocomplete", "list");
  await expect(field).toHaveAttribute("aria-haspopup", "listbox");
  // Collapsed until a keystroke produces something to show — the pre-filled
  // default address must not open a list nobody asked for.
  await expect(field).toHaveAttribute("aria-expanded", "false");
});

test("route cards expose their selected state", async ({ page }) => {
  await openApp(page);

  const cards = page.getByLabel("Suggested walks").getByRole("button");
  await expect(cards.first()).toHaveAttribute("aria-pressed", "true");
  await expect(cards.nth(1)).toHaveAttribute("aria-pressed", "false");
});

test("progress bars report their value to assistive technology", async ({ page }) => {
  await openApp(page);

  const bars = page.getByRole("progressbar");
  await expect(bars.first()).toHaveAttribute("aria-valuenow", /\d+/);
});

test("the elevation chart has a text alternative", async ({ page }) => {
  await openApp(page);
  await expect(page.getByRole("img", { name: /Elevation profile/i })).toBeVisible();
});

test("the page has exactly one h1", async ({ page }) => {
  await openApp(page);
  await expect(page.locator("h1")).toHaveCount(1);
});
