/**
 * Client for the StepWise API.
 *
 * The base URL is not known at build time — it is an AWS-issued execute-api
 * domain that only exists once the CDK stack has deployed. So the static export
 * ships without it and reads `/config.json`, which CDK writes into the site
 * bucket from the real CloudFormation outputs. That file is served with caching
 * disabled, so a redeploy that moves the API takes effect immediately.
 */

import type { PlanResponse, Preferences, ProfileInput, RegionsResponse } from "./types";

/** An API failure carrying the status and whatever detail the server returned. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  /**
   * Flatten the geocoder's per-region street suggestions into one list.
   *
   * A miss returns `{sf: [...], pia: [...]}`; the user wants "did you mean",
   * not a breakdown by region they never asked about.
   */
  suggestions(limit = 4): string[] {
    const raw = this.detail.suggestions;
    if (!raw || typeof raw !== "object") return [];
    return Object.values(raw as Record<string, unknown>)
      .flatMap((value) => (Array.isArray(value) ? (value as string[]) : []))
      .slice(0, limit);
  }
}

export interface Config {
  apiBaseUrl: string;
  env: string;
  version: string;
  region: string;
}

let configPromise: Promise<Config> | null = null;

/**
 * Fetch and memoise the runtime config.
 *
 * Falls back to a local API under `next dev`, which is the only place the two
 * environments are allowed to differ.
 */
export function loadConfig(): Promise<Config> {
  if (!configPromise) {
    configPromise = fetch("/config.json", { cache: "no-store" })
      .then((response) => {
        if (!response.ok) {
          throw new ApiError(`config.json returned ${response.status}`, response.status);
        }
        return response.json() as Promise<Config>;
      })
      .catch((error) => {
        if (process.env.NODE_ENV === "development") {
          return { apiBaseUrl: "http://127.0.0.1:8000", env: "local", version: "dev", region: "local" };
        }
        throw error;
      });
  }
  return configPromise;
}

/** Clear the memoised config. Used by tests; never called by the app. */
export function resetConfig(): void {
  configPromise = null;
}

/**
 * Issue one API request and parse its JSON.
 *
 * Every call is logged with its status and timing, mirroring the DEBUG logging
 * on the server, so a slow or failing request can be diagnosed from the browser
 * console without opening CloudWatch.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const config = await loadConfig();
  const url = `${config.apiBaseUrl}${path}`;
  const started = typeof performance !== "undefined" ? performance.now() : 0;

  const response = await fetch(url, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // An empty or non-JSON body is handled by the status check below.
  }

  const elapsed = Math.round(
    (typeof performance !== "undefined" ? performance.now() : 0) - started,
  );
  console.debug(
    "[stepwise] %s %s -> %d in %dms",
    init?.method ?? "GET",
    url,
    response.status,
    elapsed,
  );

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

/** `GET /v1/regions` — the coverage areas. */
export function getRegions(): Promise<RegionsResponse> {
  return request<RegionsResponse>("/v1/regions");
}

export interface PlanRequest {
  address?: string;
  lat?: number;
  lon?: number;
  region?: string;
  minutes: number;
  profile: ProfileInput;
  preferences: Preferences;
  max_routes?: number;
}

/** `POST /v1/plan` — the product. */
export function planWalk(body: PlanRequest): Promise<PlanResponse> {
  return request<PlanResponse>("/v1/plan", { method: "POST", body: JSON.stringify(body) });
}
