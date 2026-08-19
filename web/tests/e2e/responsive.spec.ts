import { expect, test, type Locator, type Page } from "@playwright/test";

// These stub the network, so they describe the bundle's behaviour rather than a
// deployment's. Running them against E2E_BASE_URL would intercept the real API
// and prove nothing; `deployed.spec.ts` covers that case instead.
test.skip(
  Boolean(process.env.E2E_BASE_URL),
  "stubbed specs run against the local export only",
);
import { stubApi } from "./fixtures";

/**
 * The layout, at every size the product claims to support.
 *
 * Nothing here hard-codes a width. Each assertion is a property that must hold
 * whatever the viewport is, and `playwright.config.ts` supplies the sizes as
 * projects — 320x568, 390x844, 768x1024, 1024x768, 1440x900, 1920x1080 and
 * 2560x1440, plus the desktop and Pixel 7 profiles the rest of the suite uses.
 * Written that way because a breakpoint bug is never "the assertion was wrong at
 * 768"; it is "the layout stopped working somewhere between two sizes".
 *
 * The map's *height* is asserted explicitly and everywhere. A collapsed map is
 * the one layout failure this codebase has actually shipped: MapLibre's
 * unlayered CSS overrode Tailwind's `absolute`, the container fell to zero
 * height, and every network request still succeeded. A presence check would
 * have passed throughout.
 */

/** How small a rendered map is still allowed to be, in CSS pixels. */
const MIN_MAP_PX = 100;

/**
 * Assert an element sits wholly inside the viewport.
 *
 * The one-pixel slack absorbs sub-pixel layout rounding, which otherwise makes
 * this flaky at fractional device scale factors rather than wrong.
 */
async function expectWithinViewport(page: Page, locator: Locator, what: string) {
  const viewport = page.viewportSize();
  expect(viewport, "the projects all set an explicit viewport").not.toBeNull();
  const box = await locator.boundingBox();
  expect(box, `${what} should have a layout box`).not.toBeNull();

  expect(box!.x, `${what} runs off the left edge`).toBeGreaterThanOrEqual(-1);
  expect(box!.y, `${what} runs off the top edge`).toBeGreaterThanOrEqual(-1);
  expect(box!.x + box!.width, `${what} runs off the right edge`).toBeLessThanOrEqual(
    viewport!.width + 1,
  );
  expect(box!.y + box!.height, `${what} runs off the bottom edge`).toBeLessThanOrEqual(
    viewport!.height + 1,
  );
}

/**
 * Assert the page has no horizontal scrollbar.
 *
 * Sideways scroll on a map app is always a bug: it means something is wider
 * than the screen, and on a phone it fights the map's own pan gesture.
 */
async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth };
  });
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1);
}

/** The rendered size of the map container, once it has been given one. */
async function mapBox(page: Page) {
  const map = page.getByLabel("Route map");
  await expect(map).toBeVisible();
  const box = await map.boundingBox();
  expect(box, "the map container should have a layout box").not.toBeNull();
  return box!;
}

test.beforeEach(async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
});

test("the map is drawn at a usable size", async ({ page }) => {
  const box = await mapBox(page);
  expect(box.width).toBeGreaterThan(MIN_MAP_PX);
  expect(box.height).toBeGreaterThan(MIN_MAP_PX);

  // MapLibre's own canvas must have taken the container's size, not merely be
  // present: a canvas can exist inside a collapsed container.
  const canvas = page.locator("canvas.maplibregl-canvas");
  await expect(canvas).toBeVisible();
  const canvasBox = await canvas.boundingBox();
  expect(canvasBox?.height ?? 0).toBeGreaterThan(MIN_MAP_PX);
});

test("the map and the panel are both on screen at once", async ({ page }) => {
  const map = await mapBox(page);
  const panel = await page.getByLabel("Plan a walk").boundingBox();
  expect(panel).not.toBeNull();

  // The panel may float over the map on a wide screen, but it must never be
  // the only thing visible: on a phone the map takes the top of the column, and
  // above `sm` the panel is capped well short of the full width.
  const covered = Math.max(
    0,
    Math.min(map.x + map.width, panel!.x + panel!.width) - Math.max(map.x, panel!.x),
  ) * Math.max(
    0,
    Math.min(map.y + map.height, panel!.y + panel!.height) - Math.max(map.y, panel!.y),
  );
  expect(covered).toBeLessThan(map.width * map.height * 0.9);
});

test("the address field and the submit button are reachable and on screen", async ({ page }) => {
  const address = page.getByLabel(/Start address/i);
  await address.scrollIntoViewIfNeeded();
  await expect(address).toBeVisible();
  await expectWithinViewport(page, address, "the address input");

  const submit = page.getByRole("button", { name: /find me a walk/i });
  await submit.scrollIntoViewIfNeeded();
  await expect(submit).toBeVisible();
  await expect(submit).toBeEnabled();
  await expectWithinViewport(page, submit, "the submit button");
});

test("the page does not scroll sideways", async ({ page }) => {
  await expectNoHorizontalScroll(page);
});

test("the legend stays clear of the panel and of the edges", async ({ page }) => {
  const legend = page.getByLabel("Surface colour key");

  // It is deliberately hidden below `sm`, where the map pane is too short to
  // spend sixty pixels on a key.
  if ((await legend.count()) === 0 || !(await legend.isVisible())) return;

  await expectWithinViewport(page, legend, "the legend");
  const legendBox = (await legend.boundingBox())!;
  const panelBox = (await page.getByLabel("Plan a walk").boundingBox())!;

  const overlapsHorizontally =
    legendBox.x < panelBox.x + panelBox.width && panelBox.x < legendBox.x + legendBox.width;
  const overlapsVertically =
    legendBox.y < panelBox.y + panelBox.height && panelBox.y < legendBox.y + legendBox.height;
  expect(overlapsHorizontally && overlapsVertically, "the legend overlaps the panel").toBe(false);
});

test("planning a walk leaves the results reachable and the map intact", async ({ page }) => {
  const address = page.getByLabel(/Start address/i);
  await address.scrollIntoViewIfNeeded();
  await address.fill("100 N Main St, Morton, IL");

  const submit = page.getByRole("button", { name: /find me a walk/i });
  await submit.scrollIntoViewIfNeeded();
  await submit.click();

  const results = page.getByLabel("Suggested walks");
  await results.scrollIntoViewIfNeeded();
  await expect(results).toBeVisible();

  // "Reachable" means a user can actually get a card in front of their eyes,
  // not merely that it exists in the DOM inside an overflowing box. The
  // selected card expands to a chart and four health cards, which is taller
  // than a small phone on purpose — so the property is that it fits the width
  // and that a usable band of it is on screen, not that all of it is.
  const firstCard = results.getByRole("button").first();
  await firstCard.scrollIntoViewIfNeeded();
  await expect(firstCard).toBeVisible();

  const viewport = page.viewportSize()!;
  const cardBox = (await firstCard.boundingBox())!;
  expect(cardBox.x, "a route card runs off the left edge").toBeGreaterThanOrEqual(-1);
  expect(cardBox.x + cardBox.width, "a route card runs off the right edge").toBeLessThanOrEqual(
    viewport.width + 1,
  );
  const visibleHeight =
    Math.min(cardBox.y + cardBox.height, viewport.height) - Math.max(cardBox.y, 0);
  expect(visibleHeight, "no usable part of the first route card is on screen").toBeGreaterThan(80);

  // The results are the tallest thing the panel ever holds. If growing it
  // squeezed the map, this is where it would show.
  const box = await mapBox(page);
  expect(box.width).toBeGreaterThan(MIN_MAP_PX);
  expect(box.height).toBeGreaterThan(MIN_MAP_PX);

  await expectNoHorizontalScroll(page);
});
