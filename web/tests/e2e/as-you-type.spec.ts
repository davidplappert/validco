import { expect, test, type Page } from "@playwright/test";

// These stub the network, so they describe the bundle's behaviour rather than a
// deployment's. Running them against E2E_BASE_URL would intercept the real API
// and prove nothing; `deployed.spec.ts` covers that case instead.
test.skip(Boolean(process.env.E2E_BASE_URL), "stubbed specs run against the local export only");
import { PLAN_RESPONSE, SUGGESTIONS, stubApi, stubSuggest } from "./fixtures";
import {
  addressField,
  openApp,
  planAfter,
  planFor,
  planPayload,
  recordPlans,
  settle,
  updatingOverlay,
} from "./support/form";

/**
 * The form with no button: what it does, and what it is careful not to do.
 *
 * The rest of the suite was adapted so it would keep passing. This file exists
 * because "the suite still passes" is not the same claim as "the new behaviour
 * works", and every property here is one that only became possible — or only
 * became a risk — when the submit button went away:
 *
 * - a request per keystroke is the obvious failure mode of an auto-submitting
 *   form, and the only defence is a debounce that nothing observes directly;
 * - the address field grew a completion list, which is a keyboard interface
 *   before it is a mouse one;
 * - and a form that recomputes every few seconds needs a busy indicator that
 *   appears for the slow case *and stays away* for the fast one, because a
 *   loader that strobes several times a minute reads as breakage.
 */

/** Mirrors `TYPING_DELAY_MS` — the longest wait the form ever takes. */
const LONGEST_DELAY_MS = 700;

/**
 * Record whether the busy scrim is ever in the document.
 *
 * Absence over a window cannot be asserted by looking once, and polling for it
 * is a race by construction. A mutation observer installed before the bundle
 * runs sees every appearance, however brief, so the assertion afterwards is
 * about the whole run rather than about the instant the assertion happened to
 * look.
 */
async function watchForOverlay(page: Page) {
  await page.addInitScript(() => {
    const flag = window as unknown as { __overlaySeen?: boolean };
    flag.__overlaySeen = false;
    const look = () => {
      if (document.querySelector('[role="status"][aria-busy="true"]')) flag.__overlaySeen = true;
    };
    new MutationObserver(look).observe(document, { childList: true, subtree: true });
    look();
  });
}

/** Whether the scrim appeared at any point since the page loaded. */
function overlayWasSeen(page: Page): Promise<boolean> {
  return page.evaluate(
    () => (window as unknown as { __overlaySeen?: boolean }).__overlaySeen === true,
  );
}

test("typing a weight digit by digit issues exactly one plan request", async ({ page }) => {
  // The literal case from the brief: 3, then 6, then 0. Un-debounced this is
  // three plans, two of them for a body nobody described.
  await stubApi(page);
  await openApp(page);
  const plans = recordPlans(page);

  const weight = page.getByLabel("Weight (lb)");
  const request = await planAfter(page, async () => {
    await weight.fill("");
    await weight.pressSequentially("360", { delay: 80 });
  });

  expect(planPayload(request).profile?.weight_lb).toBe(360);

  // Then give the form a clear run at its longest delay and count again. This
  // is the one place a fixed wait is the assertion rather than a workaround:
  // the claim is that nothing *else* happens, and there is no event to await
  // for something that must never occur.
  await page.waitForTimeout(LONGEST_DELAY_MS + 300);
  expect(plans.map((plan) => plan.profile?.weight_lb)).toEqual([360]);
});

test("typing a whole address one character at a time still plans once", async ({ page }) => {
  // The stronger version of the same claim. Every prefix of a weight below 55
  // is rejected by the form's own completeness check, so some of that
  // coalescing is free; here every prefix past "100 " is a *valid* request the
  // debounce is the only thing suppressing.
  await stubApi(page);
  await openApp(page);
  const plans = recordPlans(page);

  const request = await planFor(page, "100 N Main St, Morton, IL", { delay: 40 });
  expect(planPayload(request).address).toBe("100 N Main St, Morton, IL");

  await page.waitForTimeout(LONGEST_DELAY_MS + 300);
  expect(plans).toHaveLength(1);
});

test("the completion list opens, navigates by keyboard, and plans what is chosen", async ({
  page,
}) => {
  await stubApi(page);
  await stubSuggest(page, SUGGESTIONS);
  await openApp(page);

  const field = addressField(page);
  // Absorb the plan the typed text triggers on its own before touching the
  // list, so the request asserted below can only be the one the *choice* made.
  await planFor(page, "1100 Cal");

  const list = page.getByRole("listbox", { name: "Address suggestions" });
  await expect(list).toBeVisible();
  await expect(field).toHaveAttribute("aria-expanded", "true");
  const options = list.getByRole("option");
  await expect(options).toHaveCount(SUGGESTIONS.length);

  // ArrowDown highlights, and the input points at what is highlighted —
  // `aria-activedescendant` is how a screen reader is told which row is live
  // while focus stays in the text field.
  await field.press("ArrowDown");
  await expect(options.first()).toHaveAttribute("aria-selected", "true");
  const firstId = await options.first().getAttribute("id");
  await expect(field).toHaveAttribute("aria-activedescendant", firstId ?? "");

  await field.press("ArrowDown");
  await expect(options.nth(1)).toHaveAttribute("aria-selected", "true");
  await expect(options.first()).toHaveAttribute("aria-selected", "false");

  // Enter takes the highlighted row: the field shows it, the list closes, and
  // the plan goes out with the coordinates the suggestion already carried
  // rather than the text — the server is not asked to geocode a string it just
  // produced.
  const request = await planAfter(page, () => field.press("Enter"));
  await expect(field).toHaveValue(SUGGESTIONS[1].value);
  await expect(list).toHaveCount(0);
  await expect(field).toHaveAttribute("aria-expanded", "false");

  const payload = planPayload(request);
  expect(payload.lat).toBe(SUGGESTIONS[1].lat);
  expect(payload.lon).toBe(SUGGESTIONS[1].lon);
  expect(payload.region).toBe(SUGGESTIONS[1].region);
  expect(payload.address).toBeUndefined();
});

test("Escape dismisses the completion list without planning anything new", async ({ page }) => {
  await stubApi(page);
  await stubSuggest(page, SUGGESTIONS);
  await openApp(page);

  const field = addressField(page);
  await planFor(page, "1100 Cal");
  const plans = recordPlans(page);

  await expect(page.getByRole("listbox", { name: "Address suggestions" })).toBeVisible();
  await field.press("Escape");
  await expect(page.getByRole("listbox", { name: "Address suggestions" })).toHaveCount(0);

  // Dismissing an offer is not a decision about the address, so nothing is
  // re-planned.
  await page.waitForTimeout(LONGEST_DELAY_MS + 300);
  expect(plans).toEqual([]);
});

test("a fast plan shows no overlay at all", async ({ page }) => {
  // A Morton 30-minute plan comes back in about four milliseconds. A loader
  // that appears and vanishes inside that window is pure flicker, and on a form
  // that recomputes as you type it would flicker every few seconds.
  await stubApi(page);
  await watchForOverlay(page);
  await openApp(page);

  await planFor(page, "100 N Main St, Morton, IL");
  await expect(page.getByLabel("Suggested walks")).toBeVisible();

  expect(await overlayWasSeen(page), "the scrim appeared for a plan nobody waited for").toBe(false);
});

test("a slow plan shows the overlay, without blocking the page", async ({ page }) => {
  await stubApi(page, 200, PLAN_RESPONSE, 1_200);
  await openApp(page, { timeout: 15_000 });

  const overlay = updatingOverlay(page);
  await addressField(page).fill("100 N Main St, Morton, IL");
  await expect(overlay).toBeVisible();

  // It is a scrim, not a screen: `pointer-events-none` is what makes every
  // other promise about it true for free — it cannot swallow a click, trap
  // focus or eat the next keystroke.
  await expect(overlay).toHaveCSS("pointer-events", "none");
  // And the last result stays legible underneath rather than being blanked.
  await expect(page.getByLabel("Suggested walks")).toBeVisible();

  await expect(overlay).toHaveCount(0, { timeout: 15_000 });
  await settle(page, 15_000);
});
