/**
 * Shapes returned by the StepWise API.
 *
 * Kept apart from the fetch client so components can import the types without
 * pulling in anything that touches `window` or `fetch` — which is what lets the
 * component tests run in a plain jsdom environment.
 */

export type Sex = "male" | "female";
export type Intensity = "light" | "moderate" | "vigorous";
export type Surface = "path" | "sidewalk" | "crossing" | "road";

/** Runtime configuration, written into the site bucket by CDK at deploy time. */
export interface Config {
  apiBaseUrl: string;
  env: string;
  version: string;
  region: string;
}

/** One coverage area and the size of its baked datasets. */
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

/** The health inputs the form collects. */
export interface ProfileInput {
  sex: Sex;
  age: number;
  weight_lb: number;
  height_ft?: number;
  height_in?: number;
}

/** The five routing preference toggles. */
export interface Preferences {
  prefer_paths: boolean;
  avoid_hills: boolean;
  avoid_stairs: boolean;
  avoid_busy_roads: boolean;
  prefer_green: boolean;
}

/** What the API derived about the walker. */
export interface DerivedProfile {
  sex: Sex;
  age_years: number;
  weight_kg: number;
  weight_lb: number;
  height_cm: number;
  height_assumed: boolean;
  bmi: number;
  bmi_class: string;
  body_fat_pct_est: number;
  fat_free_mass_kg: number;
  rmr_kcal_day: number;
  baseline_speed_ms: number;
  baseline_speed_mph: number;
  step_length_m: number;
}

/** The physiological cost of one walk. */
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
  intensity: Intensity;
  avg_speed_ms: number;
  avg_speed_mph: number;
  avg_pace_min_per_mi: number;
  peak_grade_pct: number;
  climb_per_km_m: number;
  knee_load_peak_bw: number;
}

export interface GuidelineProgress {
  who_weekly_moderate_min: number;
  who_weekly_upper_min: number;
  moderate_minutes: number;
  pct_of_weekly_target: number;
  met_minutes: number;
  counts_as_moderate: boolean;
}

export interface StepProgress {
  walk_steps: number;
  daily_target: number;
  pct_of_daily_target: number;
  context: string;
}

export interface EnergyReport {
  kcal_gross: number;
  kcal_net_of_resting: number;
  note: string;
}

export interface JointLoadReport {
  peak_knee_force_bw: number;
  peak_knee_force_kg: number;
  peak_knee_force_lb: number;
  note: string;
}

/** One frequency's projected weight change. */
export interface WeightProjectionEntry {
  sessions_per_week: number;
  weekly_kcal: number;
  first_month_lb: number;
  one_year_lb: number;
  eventual_lb: number;
  eventual_pct_of_body_weight: number;
  /** False when the weekly deficit is too small to project honestly. */
  meaningful: boolean;
}

/** Projected body-weight change from repeating a walk. */
export interface WeightProjection {
  projections: WeightProjectionEntry[];
  basis: string;
  note: string;
  method: string;
  caveats: string[];
}

/** Everything the app is willing to claim about a walk, with its caveats. */
export interface Health {
  guideline_progress: GuidelineProgress;
  steps: StepProgress;
  energy: EnergyReport;
  joint_load: JointLoadReport;
  weight_projection: WeightProjection;
  caveats: string[];
}

export interface Suitability {
  score: number;
  notes: string[];
}

export interface Destination {
  name: string;
  category: string;
  group: string;
  lat: number;
  lon: number;
  straight_line_m: number;
}

/** A run of the route sharing one surface class, for colour-coded drawing. */
export interface RouteSegment {
  surface: string;
  coordinates: [number, number][];
}

export interface RouteGeometry {
  type: "LineString";
  coordinates: [number, number][];
  segments: RouteSegment[];
}

export interface ElevationPoint {
  m: number;
  ele: number;
}

/** One suggested walk. */
export interface Route {
  id: number;
  shape: string;
  score: number;
  destination: Destination | null;
  effort: Effort;
  health: Health;
  suitability: Suitability;
  surface_breakdown_pct: Record<string, number>;
  surface_labels: Record<string, string>;
  features: string[];
  streets: string[];
  geometry: RouteGeometry;
  elevation_profile: ElevationPoint[];
}

export interface Origin {
  lat: number;
  lon: number;
  snapped_lat: number;
  snapped_lon: number;
  snap_distance_m: number;
  label?: string;
  match?: string;
}

/** The full response from `POST /v1/plan`. */
export interface PlanResponse {
  region: string;
  origin: Origin;
  profile: DerivedProfile;
  request: { minutes: number; preferences: Preferences };
  routes: Route[];
  timing_ms: { plan: number };
  attribution: string;
}

export interface RegionsResponse {
  regions: Region[];
  pending?: RegionState[];
  default: string;
  on_demand?: boolean;
  attribution: string;
}

/** Lifecycle of a coverage area that is being extracted on demand. */
export type RegionLifecycle = "pending" | "building" | "ready" | "failed";

/** Build stages, in the order they run. */
export type RegionStage =
  | "queued"
  | "resolve"
  | "segments"
  | "addresses"
  | "places"
  | "green"
  | "graph"
  | "terrain"
  | "pack"
  | "upload"
  | "ready";

/** The state of one on-demand coverage area. */
export interface RegionState {
  key: string;
  label: string;
  state: RegionLifecycle;
  progress: number;
  stage: RegionStage | string;
  message: string;
  bbox?: [number, number, number, number] | null;
  center?: [number, number] | null;
  stats?: {
    n_nodes?: number;
    n_edges?: number;
    n_addresses?: number;
    n_places?: number;
    build_seconds?: number;
  };
  error?: string;
  existing?: boolean;
  poll?: string;
}

/** A recovery step the API offers alongside an error. */
export interface ErrorAction {
  kind: "add_region" | "retry_region" | "poll_region";
  label: string;
  place?: string;
  key?: string;
  lat?: number;
  lon?: number;
}

/** The structured error body every failed request returns. */
export interface ApiErrorBody {
  error: string;
  code: string;
  title: string;
  detail: string;
  action?: ErrorAction;
  suggestions?: string[];
  covered?: string[];
  request_id?: string;
}
