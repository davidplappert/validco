/**
 * Client for the StepWise API.
 *
 * The API's base URL is not known at build time — it is an AWS-issued
 * execute-api domain that only exists once the CDK stack has deployed. So the
 * static export ships without it and reads `/config.json`, which CDK writes
 * into the site bucket from the real CloudFormation values. That file is served
 * with caching disabled so a redeploy that moves the API takes effect at once.
 */

export type Sex = "male" | "female";

export interface Config {
  apiBaseUrl: string;
  env: string;
  version: string;
  region: string;
}

export interface Region {
  key: string;
  label: string;
  center: [number, number];
  bbox: [number, number, number, number];
  n_nodes: number;
  n_edges: number;
  n_addresses: number;
  n_places: number;
}

export interface ProfileInput {
  sex: Sex;
  age: number;
  weight_lb: number;
  height_ft?: number;
  height_in?: number;
}

export interface Preferences {
  prefer_paths: boolean;
  avoid_hills: boolean;
  avoid_stairs: boolean;
  avoid_busy_roads: boolean;
  prefer_green: boolean;
}

export interface Effort {
  distance_m: number;
  distance_mi: number;
  duration_s: number;
  duration_min: number;
  ascent_m: number;
  descent_m: number;
  ascent_ft: number;
  descent_ft: number;
  kcal_gross: number;
  kcal_net: number;
  steps: number;
  mets: number;
  personal_mets: number;
  intensity: "light" | "moderate" | "vigorous";
  avg_speed_mph: number;
  avg_pace_min_per_mi: number;
  peak_grade_pct: number;
  knee_load_peak_bw: number;
}

export interface Health {
  guideline_progress: {
    who_weekly_moderate_min: number;
    moderate_minutes: number;
    pct_of_weekly_target: number;
    met_minutes: number;
    counts_as_moderate: boolean;
  };
  steps: {
    walk_steps: number;
    daily_target: number;
    pct_of_daily_target: number;
    context: string;
  };
  energy: { kcal_gross: number; kcal_net_of_resting: number; note: string };
  joint_load: {
    peak_knee_force_bw: number;
    peak_knee_force_lb: number;
    note: string;
  };
  caveats: string[];
}

export interface Destination {
  name: string;
  category: string;
  group: string;
  lat: number;
  lon: number;
  straight_line_m: number;
}

export interface RouteSegment {
  surface: string;
  coordinates: [number, number][];
}

export interface Route {
  id: number;
  shape: string;
  score: number;
  destination: Destination | null;
  effort: Effort;
  health: Health;
  suitability: { score: number; notes: string[] };
  surface_breakdown_pct: Record<string, number>;
  surface_labels: Record<string, string>;
  features: string[];
  streets: string[];
  geometry: { type: "LineString"; coordinates: [number, number][]; segments: RouteSegment[] };
  elevation_profile: { m: number; ele: number }[];
}

export interface PlanResponse {
  region: string;
  origin: {
    lat: number;
    lon: number;
    snapped_lat: number;
    snapped_lon: number;
    snap_distance_m: number;
    label?: string;
    match?: string;
  };
  profile: {
    bmi: number;
    bmi_class: string;
    baseline_speed_mph: number;
    rmr_kcal_day: number;
    height_assumed: boolean;
    body_fat_pct_est: number;
  };
  request: { minutes: number; preferences: Preferences };
  routes: Route[];
  timing_ms: { plan: number };
  attribution: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let configPromise: Promise<Config> | null = null;

export function loadConfig(): Promise<Config> {
  if (!configPromise) {
    configPromise = fetch("/config.json", { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new ApiError(`config.json returned ${r.status}`, r.status);
        return r.json() as Promise<Config>;
      })
      .catch((err) => {
        // `next dev` has no config.json, so fall back to a local API. This is
        // the only place the two environments differ.
        if (process.env.NODE_ENV === "development") {
          return {
            apiBaseUrl: "http://127.0.0.1:8000",
            env: "local",
            version: "dev",
            region: "local",
          };
        }
        throw err;
      });
  }
  return configPromise;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const config = await loadConfig();
  const url = `${config.apiBaseUrl}${path}`;
  const started = performance.now();
  const response = await fetch(url, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  const elapsed = Math.round(performance.now() - started);

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    /* an empty or non-JSON body is handled by the status check below */
  }

  // Logging every call with its timing mirrors the DEBUG logging on the server
  // side, so a slow or failing request can be diagnosed from the browser
  // console without reaching for CloudWatch.
  console.debug("[stepwise] %s %s -> %d in %dms", init?.method ?? "GET", url, response.status, elapsed);

  if (!response.ok) {
    const detail = (body ?? {}) as Record<string, unknown>;
    throw new ApiError(
      typeof detail.error === "string" ? detail.error : `request failed (${response.status})`,
      response.status,
      detail,
    );
  }
  return body as T;
}

export function getRegions(): Promise<{ regions: Region[]; default: string; attribution: string }> {
  return request("/v1/regions");
}

export function plan(body: {
  address?: string;
  lat?: number;
  lon?: number;
  region?: string;
  minutes: number;
  profile: ProfileInput;
  preferences: Preferences;
  max_routes?: number;
}): Promise<PlanResponse> {
  return request("/v1/plan", { method: "POST", body: JSON.stringify(body) });
}
