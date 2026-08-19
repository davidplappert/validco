import { expect, test, type Page } from "@playwright/test";

// Stubbed, so these describe the bundle rather than a deployment. Pointed at a
// real API they would intercept the very endpoints under test.
test.skip(
  Boolean(process.env.E2E_BASE_URL),
  "stubbed specs run against the local export only",
);
import { API_HOST, PLAN_RESPONSE, stubApi } from "./fixtures";

/**
 * Building coverage on demand.
 *
 * The product claim is that any address works, at the cost of a one-off wait
 * the first time an area is asked for. That is a sequence — refusal, offer,
 * progress, retry — and every step of it is stubbed here, because the failure
 * mode that matters is the user being left on one of the middle steps.
 */

const KEY = "cupertino_ca";
const PLACE = "1 Infinite Loop, Cupertino, CA";

const NOT_COVERED = {
  error: `no coverage for '${PLACE}'`,
  code: "region_not_covered",
  title: "We don't have walking data for that area yet",
  detail: "We can pull it from Overture Maps now — it takes a minute or two.",
  covered: ["San Francisco, CA", "Peoria & Morton, IL"],
  action: { kind: "add_region", label: "Add this area", place: PLACE },
};

function status(overrides: Record<string, unknown> = {}) {
  return {
    key: KEY,
    label: "Cupertino, CA",
    state: "building",
    progress: 0.15,
    stage: "segments",
    message: "Downloading streets and paths…",
    ...overrides,
  };
}

/** The plan that lands once the area exists, distinguishable from the default. */
const CUPERTINO_PLAN = {
  ...PLAN_RESPONSE,
  region: KEY,
  origin: { ...PLAN_RESPONSE.origin, label: "1 Infinite Loop, Cupertino" },
};

/**
 * Wire up the whole build lifecycle.
 *
 * `states` is what `GET /v1/regions/{key}` reports, one entry per poll with the
 * last repeating; the plan endpoint starts refusing Cupertino and starts
 * succeeding the moment a `ready` status has been served, which is the coupling
 * the test is really about.
 */
async function stubBuild(page: Page, states: Record<string, unknown>[]) {
  const polls: number[] = [];
  const posts: string[] = [];
  const deletes: string[] = [];
  let ready = false;

  await stubApi(page);

  await page.route(`${API_HOST}/v1/plan`, (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    const cupertino = String(body.address ?? "").includes("Cupertino");
    if (cupertino && !ready) {
      return route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify(NOT_COVERED),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(cupertino ? CUPERTINO_PLAN : PLAN_RESPONSE),
    });
  });

  // Registered after stubApi's, so this one wins for the POST.
  await page.route(`${API_HOST}/v1/regions`, (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    posts.push(route.request().postData() ?? "");
    return route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify(status({ state: "pending", progress: 0.05, stage: "queued", message: "Queued." })),
    });
  });

  await page.route(`${API_HOST}/v1/regions/*`, (route) => {
    if (route.request().method() === "DELETE") {
      deletes.push(route.request().url());
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ key: KEY, cleared: true }),
      });
    }
    const next = states[Math.min(polls.length, states.length - 1)];
    polls.push(Date.now());
    if (next.state === "ready") ready = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(next),
    });
  });

  return { polls, posts, deletes };
}

/** Type the uncovered address and ask for a walk. */
async function planCupertino(page: Page) {
  await page.goto("/");
  await expect(page.getByLabel("Suggested walks")).toBeVisible();
  await page.getByLabel(/Start address/i).fill(PLACE);
  await page.getByRole("button", { name: /find me a walk/i }).click();
}

test("an uncovered address is built on demand, then planned automatically", async ({ page }) => {
  // Each state is served twice so it is on screen for two poll intervals; a
  // state visible for a single 1s tick would make the assertions a race.
  const calls = await stubBuild(page, [
    status({ progress: 0.15 }),
    status({ progress: 0.15 }),
    status({ progress: 0.45, stage: "graph", message: "Building the walking network…" }),
    status({ progress: 0.45, stage: "graph", message: "Building the walking network…" }),
    status({ progress: 0.8, stage: "terrain", message: "Sampling elevation…" }),
    status({ progress: 0.8, stage: "terrain", message: "Sampling elevation…" }),
    status({ state: "ready", progress: 1, stage: "ready", message: "Ready." }),
  ]);

  await planCupertino(page);

  // The refusal is an offer, not a dead end.
  const prompt = page.getByRole("alert", { name: "Coverage needed" });
  await expect(prompt).toBeVisible();
  await expect(prompt).toContainText("We don't have walking data for that area yet");
  await expect(prompt).toContainText("takes a minute or two");
  await expect(page.getByRole("alert", { name: "Planning error" })).toHaveCount(0);

  await prompt.getByRole("button", { name: "Add this area" }).click();

  // A determinate bar, advancing through the states the server reports.
  const bar = page.getByRole("progressbar", { name: /Building Cupertino/ });
  await expect(bar).toBeVisible();
  await expect(bar).toHaveAttribute("aria-valuenow", "15");
  await expect(page.getByRole("status", { name: "Building coverage" })).toContainText(
    "Downloading streets and paths…",
  );
  await expect(bar).toHaveAttribute("aria-valuenow", "45");
  await expect(bar).toHaveAttribute("aria-valuenow", "80");

  // Ready: the original request is re-run without the user touching anything.
  await expect(page.getByText("1 Infinite Loop, Cupertino")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByLabel("Suggested walks")).toBeVisible();
  await expect(page.getByRole("status", { name: "Building coverage" })).toHaveCount(0);
  await expect(page.getByRole("alert", { name: "Coverage needed" })).toHaveCount(0);

  expect(calls.posts.length).toBe(1);
  expect(JSON.parse(calls.posts[0])).toEqual({ place: PLACE });
  expect(calls.polls.length).toBeGreaterThanOrEqual(4);

  // The area is remembered, so a return visit does not wait again.
  const stored = await page.evaluate(() => window.localStorage.getItem("stepwise.regions.v1"));
  expect(JSON.parse(stored ?? "[]")).toEqual([
    { key: KEY, label: "Cupertino, CA", addedAt: expect.any(Number) },
  ]);
});

test("a build somebody else started is joined, not restarted", async ({ page }) => {
  const calls = await stubBuild(page, [
    status({ progress: 0.6, stage: "graph", message: "Building the walking network…" }),
    status({ state: "ready", progress: 1, stage: "ready", message: "Ready." }),
  ]);
  // The plan itself reports the build in flight, so there is nothing to offer.
  await page.route(`${API_HOST}/v1/plan`, (route) => {
    const body = JSON.parse(route.request().postData() ?? "{}");
    if (!String(body.address ?? "").includes("Cupertino")) {
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PLAN_RESPONSE) });
    }
    if (calls.polls.length === 0) {
      return route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          error: `region '${KEY}' is still building`,
          code: "region_building",
          title: "Preparing this area",
          detail: "We're downloading the walking network.",
          action: { kind: "poll_region", label: "Watch progress", key: KEY },
        }),
      });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CUPERTINO_PLAN) });
  });

  await planCupertino(page);

  await expect(page.getByRole("progressbar", { name: /Building Cupertino/ })).toBeVisible();
  await expect(page.getByRole("alert", { name: "Coverage needed" })).toHaveCount(0);
  await expect(page.getByText("1 Infinite Loop, Cupertino")).toBeVisible({ timeout: 15_000 });
  // Nothing was POSTed: the build was already running.
  expect(calls.posts).toEqual([]);
});

test("a failed build offers a retry that clears it first", async ({ page }) => {
  const calls = await stubBuild(page, [
    status({ state: "failed", progress: 0.3, error: "Overture has no streets there." }),
    status({ progress: 0.5 }),
    status({ state: "ready", progress: 1, stage: "ready", message: "Ready." }),
  ]);

  await planCupertino(page);
  await page.getByRole("button", { name: "Add this area" }).click();

  const failure = page.getByRole("alert", { name: "Coverage needed" });
  await expect(failure).toContainText("We couldn't prepare that area");
  await expect(failure).toContainText("Overture has no streets there.");

  await failure.getByRole("button", { name: "Try again" }).click();

  await expect(page.getByText("1 Infinite Loop, Cupertino")).toBeVisible({ timeout: 15_000 });
  // The failed record has to go before the rebuild, or the server just repeats it.
  expect(calls.deletes.length).toBe(1);
  expect(calls.deletes[0]).toContain(`/v1/regions/${KEY}`);
  expect(calls.posts.length).toBe(2);
});
