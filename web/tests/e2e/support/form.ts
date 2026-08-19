import { expect, type Locator, type Page, type Request } from "@playwright/test";

/**
 * Driving a form that has no submit button.
 *
 * The form plans itself once the user stops typing, which makes "and then it
 * planned" a *timing* question rather than a click. Nineteen specs used to say
 * `click()`; if each of them now grew its own `waitForTimeout(700)` the suite
 * would be a minute slower and flaky in both directions — too short and the
 * request has not been made, too long and a second one has.
 *
 * So there are exactly two ways to make a plan happen here, and both wait on
 * the network rather than on the clock:
 *
 * - {@link planNow} presses the hidden submit button. That is the *skip the
 *   wait* path — the same one Enter takes — so it is the right choice whenever
 *   a spec wants a plan for the form as it already stands.
 * - {@link planAfter} performs an edit and waits for the plan that edit
 *   triggers on its own. This is the right choice whenever a **field changes**,
 *   and the reason is not stylistic: a change arms a debounce timer that will
 *   fire whether or not the test also pressed submit. A spec that edits a field
 *   and then submits gets *two* plans, the second landing several hundred
 *   milliseconds later — long enough to arrive after the assertions, cancel an
 *   in-flight region build (`page.tsx` calls `cancelRegion()` on every submit)
 *   and fail a test that had already passed. Waiting for the automatic request
 *   leaves nothing armed behind it.
 *
 * Everything here matches requests by *path*, so the same helpers work against
 * the stubbed host and against a real deployment in `deployed.spec.ts`.
 */

/** What the status line says when nothing is in flight. */
export const IDLE_STATUS = "Walks update as you type.";

/** What it says while a plan is running. */
export const BUSY_STATUS = "Updating your walks…";

/** The accessible name of the visually-hidden submit button. */
export const SUBMIT_NAME = /update walks now/i;

/** A plan request, whichever host is serving it. */
export function isPlanRequest(request: Request): boolean {
  return request.method() === "POST" && new URL(request.url()).pathname.endsWith("/v1/plan");
}

/** An address-completion request, whichever host is serving it. */
export function isSuggestRequest(request: Request): boolean {
  return new URL(request.url()).pathname.endsWith("/v1/suggest");
}

/** The address combobox. */
export function addressField(page: Page): Locator {
  return page.getByLabel(/Start address/i);
}

/**
 * The submit button, which is `sr-only` until it has focus.
 *
 * It is still in the accessibility tree and still in the tab order — that is
 * the whole point of hiding it this way — so it is found by role and name
 * exactly as a visible button would be.
 */
export function submitButton(page: Page): Locator {
  return page.getByRole("button", { name: SUBMIT_NAME });
}

/** The form's own statement of what it is doing, when it is doing nothing. */
export function idleStatus(page: Page): Locator {
  return page.getByText(IDLE_STATUS, { exact: true });
}

/** The busy scrim, which only appears for a plan slow enough to be worth one. */
export function updatingOverlay(page: Page): Locator {
  return page.getByRole("status", { name: BUSY_STATUS });
}

/**
 * Wait until no plan is in flight.
 *
 * `timeout` exists for `deployed.spec.ts`, where a cold Lambda decoding several
 * megabytes of graph arrays takes far longer than the default assertion window
 * — the stubbed suite never needs it.
 */
export async function settle(page: Page, timeout?: number): Promise<void> {
  await expect(idleStatus(page)).toBeVisible({ timeout });
}

/**
 * Load the app and wait for the plan it runs on its own.
 *
 * The opening plan is not optional — the page passes `autoRun` to `usePlanner`
 * — so a spec that starts interacting before it lands is racing it. Waiting for
 * the *response* rather than for the idle status matters: there is a render
 * before the mount effect fires in which the status line already reads idle.
 */
export async function openApp(
  page: Page,
  options: { path?: string; timeout?: number } = {},
): Promise<void> {
  const { path = "/", timeout } = options;
  const firstPlan = page.waitForResponse((response) => isPlanRequest(response.request()), {
    timeout,
  });
  await page.goto(path);
  await firstPlan;
  await settle(page, timeout);
}

/**
 * Plan immediately, skipping the debounce, the way Enter does.
 *
 * `press` rather than `click`: the button is clipped to a single pixel until it
 * is focused, so a click has a hit target a stray overlay could intercept,
 * while a key press goes to the focused element by definition. Focusing it also
 * makes `focus:not-sr-only` reveal it, which is what a keyboard user sees.
 */
export async function planNow(page: Page): Promise<Request> {
  const submit = submitButton(page);
  // Disabled while a plan runs, so this is also the wait for the previous one.
  await expect(submit).toBeEnabled();
  const request = page.waitForRequest(isPlanRequest);
  await submit.press("Enter");
  return request;
}

/**
 * Make an edit and wait for the plan it causes by itself.
 *
 * Returns the request so a caller can assert on what was sent, and settles
 * afterwards so the page is quiet before the next interaction.
 */
export async function planAfter(
  page: Page,
  edit: () => Promise<void>,
  timeout?: number,
): Promise<Request> {
  const request = page.waitForRequest(isPlanRequest, { timeout });
  await edit();
  const seen = await request;
  await settle(page, timeout);
  return seen;
}

export interface TypeOptions {
  /**
   * Milliseconds between keystrokes.
   *
   * Omitted, the value is set in one event, which is what `fill` does and what
   * a paste does. Supplied, each character is typed separately — which is the
   * only way to prove that a *sequence* of keystrokes still coalesces into one
   * request.
   */
  delay?: number;
}

/** Put an address in the combobox. */
export async function typeAddress(page: Page, text: string, options: TypeOptions = {}) {
  const field = addressField(page);
  if (options.delay === undefined) {
    await field.fill(text);
    return;
  }
  await field.fill("");
  await field.pressSequentially(text, { delay: options.delay });
}

export interface PlanOptions extends TypeOptions {
  /** Overrides the default wait, for a real deployment's cold start. */
  timeout?: number;
}

/** Type an address and wait for the plan it triggers on its own. */
export async function planFor(
  page: Page,
  address: string,
  options: PlanOptions = {},
): Promise<Request> {
  return planAfter(page, () => typeAddress(page, address, options), options.timeout);
}

/**
 * What the form sends, as far as these specs care.
 *
 * Every field is optional because the two halves are mutually exclusive by
 * design: a chosen suggestion sends `lat`/`lon` and a typed address sends
 * `address`, and asserting which one arrived is the point of more than one
 * spec here.
 */
export interface PlanBody {
  address?: string;
  lat?: number;
  lon?: number;
  region?: string;
  minutes?: number;
  profile?: {
    sex?: string;
    age?: number;
    weight_lb?: number;
    height_ft?: number;
    height_in?: number;
  };
  preferences?: Record<string, boolean>;
  max_routes?: number;
}

/** The body of a plan request, parsed. */
export function planPayload(request: Request): PlanBody {
  return JSON.parse(request.postData() ?? "{}") as PlanBody;
}

/**
 * Every plan request the page makes from here on, newest last.
 *
 * The array is live: read it *after* the thing under test has finished, which
 * is how "exactly one request" is asserted without a sleep deciding when to
 * look.
 */
export function recordPlans(page: Page): PlanBody[] {
  const payloads: PlanBody[] = [];
  page.on("request", (request) => {
    if (isPlanRequest(request)) payloads.push(planPayload(request));
  });
  return payloads;
}
