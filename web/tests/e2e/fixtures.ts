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
    destination: id === 1 ? { name: "Riverfront Park", category: "park", group: "green", lat: 40.92, lon: -89.5, straight_line_m: 500 } : null,
    effort: {
      distance_m: 2124, distance_mi: 1.32, duration_s: 1800, duration_min: 30,
      ascent_m: 4, descent_m: 4, ascent_ft: 13, descent_ft: 13,
      kcal_gross: 263, kcal_net: 208, steps: 3333, mets: 3.2, personal_mets: 4.8,
      intensity: "moderate", avg_speed_ms: 1.18, avg_speed_mph: 2.64,
      avg_pace_min_per_mi: 22.7, peak_grade_pct: 1.2, climb_per_km_m: 2,
      knee_load_peak_bw: 3.0,
    },
    health: {
      guideline_progress: {
        who_weekly_moderate_min: 150, who_weekly_upper_min: 300,
        moderate_minutes: 30, pct_of_weekly_target: 20, met_minutes: 96,
        counts_as_moderate: true,
      },
      steps: { walk_steps: 3333, daily_target: 7000, pct_of_daily_target: 47.6, context: "7,000 steps/day." },
      energy: { kcal_gross: 263, kcal_net_of_resting: 208, note: "Gross includes resting calories." },
      joint_load: { peak_knee_force_bw: 3.0, peak_knee_force_kg: 491, peak_knee_force_lb: 1083, note: "Four pounds per pound." },
      caveats: ["Estimates for a healthy adult, not medical advice."],
    },
    suitability: { score: 100 - id * 20, notes: ["Comfortable for you."] },
    surface_breakdown_pct: { road: 85.7, sidewalk: 14.3 },
    surface_labels: {
      path: "Walking path", sidewalk: "Sidewalk",
      crossing: "Street crossing", road: "Road (no sidewalk mapped)",
    },
    features: [],
    streets: ["North Main Street", "Taylor Drive"],
    geometry: {
      type: "LineString",
      coordinates: [[-89.4616, 40.6103], [-89.5031, 40.918], [-89.4616, 40.6103]],
      segments: [
        { surface: "road", coordinates: [[-89.4616, 40.6103], [-89.5031, 40.918]] },
        { surface: "sidewalk", coordinates: [[-89.5031, 40.918], [-89.4616, 40.6103]] },
      ],
    },
    elevation_profile: [{ m: 0, ele: 140.2 }, { m: 1000, ele: 144.1 }, { m: 2124, ele: 140.2 }],
    ...overrides,
  };
}

export const PLAN_RESPONSE = {
  region: "pia",
  origin: {
    lat: 40.61034, lon: -89.46161, snapped_lat: 40.6104, snapped_lon: -89.4612,
    snap_distance_m: 42, label: "100 North MAIN Street, 61550", match: "exact",
  },
  profile: {
    sex: "male", age_years: 33, weight_kg: 163.7, weight_lb: 320, height_cm: 182.9,
    height_assumed: false, bmi: 49.0, bmi_class: "obesity class III",
    body_fat_pct_est: 52.4, fat_free_mass_kg: 77.9, rmr_kcal_day: 2620,
    baseline_speed_ms: 1.184, baseline_speed_mph: 2.65, step_length_m: 0.669,
  },
  request: {
    minutes: 30,
    preferences: {
      prefer_paths: true, avoid_hills: false, avoid_stairs: false,
      avoid_busy_roads: true, prefer_green: false,
    },
  },
  routes: [buildRoute(0), buildRoute(1)],
  timing_ms: { plan: 5.3 },
  attribution: "Overture Maps Foundation / OpenStreetMap contributors.",
};

/**
 * Intercept config, the API, and the OSM tile server.
 *
 * Tiles are stubbed with a 1x1 PNG so the suite makes no external network calls
 * and cannot be flaky because tile.openstreetmap.org is slow.
 */
export async function stubApi(page: Page, planStatus = 200, planBody: unknown = PLAN_RESPONSE) {
  await page.route("**/config.json", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ apiBaseUrl: API_HOST, env: "e2e", version: "test", region: "us-east-1" }),
    }),
  );

  await page.route(`${API_HOST}/v1/regions`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(REGIONS) }),
  );

  await page.route(`${API_HOST}/v1/plan`, (route) =>
    route.fulfill({
      status: planStatus,
      contentType: "application/json",
      body: JSON.stringify(planBody),
    }),
  );

  const pixel = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    "base64",
  );
  await page.route("**/tile.openstreetmap.org/**", (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: pixel }),
  );
}
