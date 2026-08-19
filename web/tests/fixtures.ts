/**
 * Test fixtures shaped like real API responses.
 *
 * Built from an actual `/v1/plan` response for 100 N Main St rather than
 * invented, so a component test fails if the real payload shape changes.
 */

import type { Effort, Health, PlanResponse, Region, Route, Suitability } from "@/lib/types";

export const effort: Effort = {
  distance_m: 2124,
  distance_mi: 1.32,
  duration_s: 1800,
  duration_min: 30.0,
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
};

export const health: Health = {
  guideline_progress: {
    who_weekly_moderate_min: 150,
    who_weekly_upper_min: 300,
    moderate_minutes: 30.0,
    pct_of_weekly_target: 20.0,
    met_minutes: 96,
    counts_as_moderate: true,
  },
  steps: {
    walk_steps: 3333,
    daily_target: 7000,
    pct_of_daily_target: 47.6,
    context: "7,000 steps/day is where most outcomes plateau.",
  },
  energy: {
    kcal_gross: 263,
    kcal_net_of_resting: 208,
    note: "Gross includes the calories you would have burned at rest.",
  },
  joint_load: {
    peak_knee_force_bw: 3.0,
    peak_knee_force_kg: 491,
    peak_knee_force_lb: 1083,
    note: "Each pound of body weight is about four pounds of knee load per step.",
  },
  caveats: ["Estimates for a healthy adult, not medical advice."],
};

export const suitability: Suitability = {
  score: 100,
  notes: ["Grade, length and intensity all sit in a comfortable range for you."],
};

export const route: Route = {
  id: 0,
  shape: "loop",
  score: 104.9,
  destination: null,
  effort,
  health,
  suitability,
  surface_breakdown_pct: { road: 85.7, sidewalk: 14.3 },
  surface_labels: {
    path: "Walking path",
    sidewalk: "Sidewalk",
    crossing: "Street crossing",
    road: "Road (no sidewalk mapped)",
  },
  features: [],
  streets: ["North Main Street", "Taylor Drive", "West Sycamore Street"],
  geometry: {
    type: "LineString",
    coordinates: [
      [-89.5890, 40.6936],
      [-89.5031, 40.9180],
      [-89.5890, 40.6936],
    ],
    segments: [
      { surface: "road", coordinates: [[-89.5890, 40.6936], [-89.5031, 40.918]] },
      { surface: "sidewalk", coordinates: [[-89.5031, 40.918], [-89.5890, 40.6936]] },
    ],
  },
  elevation_profile: [
    { m: 0, ele: 140.2 },
    { m: 1000, ele: 144.1 },
    { m: 2124, ele: 140.2 },
  ],
};

/** A second, hillier route with a named destination and a lower fit score. */
export const hillyRoute: Route = {
  ...route,
  id: 1,
  score: 60.7,
  destination: {
    name: "Alta Plaza Park",
    category: "park",
    group: "green",
    lat: 37.7913,
    lon: -122.4383,
    straight_line_m: 620,
  },
  effort: { ...effort, ascent_ft: 277, peak_grade_pct: 14.6, duration_min: 52.9 },
  suitability: {
    score: 54,
    notes: ["Includes a 15% grade, steeper than the 5% that suits your profile."],
  },
  surface_breakdown_pct: { path: 44.8, sidewalk: 40.4, road: 9.0, crossing: 5.8 },
  features: ["stairs", "busy road"],
};

export const regions: Region[] = [
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
    label: "Peoria & Chillicothe, IL",
    center: [40.6936, -89.5890],
    bbox: [-89.8, 40.6, -89.3, 41.0],
    n_nodes: 62159,
    n_edges: 83935,
    n_addresses: 114045,
    n_places: 648,
  },
];

export const planResponse: PlanResponse = {
  region: "pia",
  origin: {
    lat: 40.6936,
    lon: -89.5890,
    snapped_lat: 40.6936,
    snapped_lon: -89.5890,
    snap_distance_m: 42,
    label: "708 North Main Street, 61523",
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
      avoid_hills: true,
      avoid_stairs: false,
      avoid_busy_roads: true,
      prefer_green: false,
    },
  },
  routes: [route, hillyRoute],
  timing_ms: { plan: 5.3 },
  attribution: "Overture Maps Foundation / OpenStreetMap contributors.",
};
