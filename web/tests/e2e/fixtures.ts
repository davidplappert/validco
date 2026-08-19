import type { Page } from "@playwright/test";

/**
 * Network stubs for the end-to-end suite.
 *
 * The static export reads `/config.json` for its API base URL, so pointing that
 * at a fake host and intercepting it is all it takes to run the real bundle
 * against deterministic data.
 */

export const API_HOST = "https://api.e2e.test";

const REGIONS = {
  regions: [
    {
      key: "sf",
      label: "San Francisco, CA",
      center: [37.7749, -122.4194],
      bbox: [-122.53, 37.69, -122.34, 37.84],
      n_nodes: 82889,
      n_edges: 123055,
      n_addresses: 394704,
      n_places: 3959,
    },
    {
      key: "pia",
      label: "Peoria & Morton, IL",
      center: [40.6103, -89.4616],
      bbox: [-89.8, 40.6, -89.3, 41.0],
      n_nodes: 62159,
      n_edges: 83935,
      n_addresses: 114045,
      n_places: 648,
    },
  ],
  default: "sf",
  attribution: "Overture Maps Foundation / OpenStreetMap contributors.",
};

function buildRoute(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    shape: "loop",
    score: 100 - id * 10,
    destination:
      id === 1
        ? {
            name: "Riverfront Park",
            category: "park",
            group: "green",
            lat: 40.92,
            lon: -89.5,
            straight_line_m: 500,
          }
        : null,
    effort: {
      distance_m: 2124,
      distance_mi: 1.32,
      duration_s: 1800,
      duration_min: 30,
      ascent_m: 4,
      descent_m: 4,
      ascent_ft: 13,
      descent_ft: 13,
      kcal_gross: 263,
      kcal_net: 208,
      steps: 3333,
      mets: 3.2,
      personal_mets: 4.8,
      intensity: "moderate",
      avg_speed_ms: 1.18,
      avg_speed_mph: 2.64,
      avg_pace_min_per_mi: 22.7,
      peak_grade_pct: 1.2,
      climb_per_km_m: 2,
      knee_load_peak_bw: 3.0,
    },
    health: {
      guideline_progress: {
        who_weekly_moderate_min: 150,
        who_weekly_upper_min: 300,
        moderate_minutes: 30,
        pct_of_weekly_target: 20,
        met_minutes: 96,
        counts_as_moderate: true,
      },
      steps: {
        walk_steps: 3333,
        daily_target: 7000,
        pct_of_daily_target: 47.6,
        context: "7,000 steps/day.",
      },
      energy: {
        kcal_gross: 263,
        kcal_net_of_resting: 208,
        note: "Gross includes resting calories.",
      },
      joint_load: {
        peak_knee_force_bw: 3.0,
        peak_knee_force_kg: 491,
        peak_knee_force_lb: 1083,
        note: "Four pounds per pound.",
      },
      caveats: ["Estimates for a healthy adult, not medical advice."],
    },
    suitability: { score: 100 - id * 20, notes: ["Comfortable for you."] },
    surface_breakdown_pct: { road: 85.7, sidewalk: 14.3 },
    surface_labels: {
      path: "Walking path",
      sidewalk: "Sidewalk",
      crossing: "Street crossing",
      road: "Road (no sidewalk mapped)",
    },
    features: [],
    streets: ["North Main Street", "Taylor Drive"],
    geometry: {
      type: "LineString",
      coordinates: [
        [-89.4616, 40.6103],
        [-89.5031, 40.918],
        [-89.4616, 40.6103],
      ],
      segments: [
        {
          surface: "road",
          coordinates: [
            [-89.4616, 40.6103],
            [-89.5031, 40.918],
          ],
        },
        {
          surface: "sidewalk",
          coordinates: [
            [-89.5031, 40.918],
            [-89.4616, 40.6103],
          ],
        },
      ],
    },
    elevation_profile: [
      { m: 0, ele: 140.2 },
      { m: 1000, ele: 144.1 },
      { m: 2124, ele: 140.2 },
    ],
    ...overrides,
  };
}

export const PLAN_RESPONSE = {
  region: "pia",
  origin: {
    lat: 40.61034,
    lon: -89.46161,
    snapped_lat: 40.6104,
    snapped_lon: -89.4612,
    snap_distance_m: 42,
    label: "100 North MAIN Street, 61550",
    match: "exact",
  },
  profile: {
    sex: "male",
    age_years: 33,
    weight_kg: 163.7,
    weight_lb: 320,
    height_cm: 182.9,
    height_assumed: false,
    bmi: 49.0,
    bmi_class: "obesity class III",
    body_fat_pct_est: 52.4,
    fat_free_mass_kg: 77.9,
    rmr_kcal_day: 2620,
    baseline_speed_ms: 1.184,
    baseline_speed_mph: 2.65,
    step_length_m: 0.669,
  },
  request: {
    minutes: 30,
    preferences: {
      prefer_paths: true,
      avoid_hills: false,
      avoid_stairs: false,
      avoid_busy_roads: true,
      prefer_green: false,
    },
  },
  routes: [buildRoute(0), buildRoute(1)],
  timing_ms: { plan: 5.3 },
  attribution: "Overture Maps Foundation / OpenStreetMap contributors.",
};

/**
 * Completions that collide with the planned origin, on purpose.
 *
 * The stubbed suites default to an *empty* completion list, and that hid a real
 * failure: against the live API the dropdown fills with the same street the
 * plan resolved to, so the origin summary's text matched several elements at
 * once and only the post-deploy check caught it. A stub that never returns
 * anything cannot exercise the DOM production actually renders.
 *
 * These entries deliberately repeat the address in {@link planResponse}'s
 * origin, so any assertion that means "the resolved start point" has to say so
 * rather than relying on the text being unique on the page.
 */
export const COLLIDING_SUGGESTIONS = [
  {
    kind: "address",
    label: "100 North MAIN Street, 61550",
    value: "100 N Main St, Morton, IL 61550",
    region: "pia",
    lat: 40.6134,
    lon: -89.4661,
  },
  {
    kind: "address",
    label: "102 North MAIN Street, 61550",
    value: "102 N Main St, Morton, IL 61550",
    region: "pia",
    lat: 40.6136,
    lon: -89.4662,
  },
];

/**
 * A completion list for `GET /v1/suggest`, for the specs that want a dropdown.
 *
 * The last entry is a street rather than an address and carries no coordinates,
 * which is the real shape of the API's answer: a street is positioned at its
 * midpoint when it has one and reports null when it does not, rather than
 * inventing a point.
 */
export const SUGGESTIONS = [
  {
    kind: "address",
    label: "1100 CALIFORNIA ST, 94108",
    value: "1100 California St, 94108",
    region: "sf",
    lat: 37.7919,
    lon: -122.4131,
  },
  {
    kind: "address",
    label: "1120 CALIFORNIA ST, 94108",
    value: "1120 California St, 94108",
    region: "sf",
    lat: 37.7921,
    lon: -122.4139,
  },
  {
    kind: "street",
    label: "CALIFORNIA ST",
    value: "California St",
    region: "sf",
    lat: null,
    lon: null,
  },
];

/**
 * Stub `GET /v1/suggest`.
 *
 * Registered by {@link stubApi} with an empty list, because the address field
 * queries this endpoint on **every keystroke** — including the ones a spec
 * types only to trigger a plan. Left unstubbed those requests leave the browser
 * for a host that does not resolve, which is slow, noisy in the console, and
 * exactly the sort of ambient failure that makes a suite flaky for reasons
 * nobody can reproduce.
 *
 * Call it again after `stubApi` to hand a spec a real dropdown: Playwright
 * gives precedence to the most recently registered route.
 */
export async function stubSuggest(page: Page, suggestions: unknown[] = []) {
  await page.route(
    (url) => url.pathname.endsWith("/v1/suggest"),
    (route) => {
      const query = new URL(route.request().url()).searchParams.get("q") ?? "";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ query, suggestions }),
      });
    },
  );
}

/**
 * Intercept config, the API, and the OSM tile server.
 *
 * Tiles are stubbed with a 1x1 PNG so the suite makes no external network calls
 * and cannot be flaky because tile.openstreetmap.org is slow.
 *
 * `planDelayMs` holds the plan response back, which is the only way to observe
 * anything that is deliberately invisible on a fast answer — the busy overlay
 * waits 250 ms before it shows itself.
 */
export async function stubApi(
  page: Page,
  planStatus = 200,
  planBody: unknown = PLAN_RESPONSE,
  planDelayMs = 0,
) {
  await page.route("**/config.json", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        apiBaseUrl: API_HOST,
        env: "e2e",
        version: "test",
        region: "us-east-1",
      }),
    }),
  );

  await page.route(`${API_HOST}/v1/regions`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REGIONS) }),
  );

  await page.route(`${API_HOST}/v1/plan`, async (route) => {
    // Node-side, so it slows the *server*, not the page: the browser is free to
    // render its loading state while this waits, which is the whole point.
    if (planDelayMs > 0) await new Promise((resolve) => setTimeout(resolve, planDelayMs));
    await route.fulfill({
      status: planStatus,
      contentType: "application/json",
      body: JSON.stringify(planBody),
    });
  });

  await stubSuggest(page);

  const pixel = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    "base64",
  );
  await page.route("**/tile.openstreetmap.org/**", (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: pixel }),
  );
}
