/**
 * Client for the StepWise API.
 *
 * The base URL is not known at build time — it is an AWS-issued execute-api
 * domain that only exists once the CDK stack has deployed. So the static export
 * ships without it and reads `/config.json`, which CDK writes into the site
 * bucket from the real CloudFormation outputs. That file is served with caching
 * disabled, so a redeploy that moves the API takes effect immediately.
 */

import type {
  ApiErrorBody,
  ErrorAction,
  PlanResponse,
  Preferences,
  ProfileInput,
  RegionState,
  RegionsResponse,
} from "./types";

/**
 * An API failure, carrying everything the server said about it.
 *
 * The server returns a machine `code`, human `title` and `detail`, and
 * sometimes an `action` describing how to recover. Branching on `code` rather
 * than on message text is what lets the UI offer "Add this area" for an
 * uncovered city and "Did you mean…" for a typo.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** The machine-readable failure kind. */
  get code(): string {
    return typeof this.detail.code === "string" ? this.detail.code : "unknown";
  }

  /** A heading safe to show a user. */
  get title(): string {
    return typeof this.detail.title === "string" ? this.detail.title : "Something went wrong";
  }

  /** A sentence explaining what to do about it. */
  get userDetail(): string {
    return typeof this.detail.detail === "string" ? this.detail.detail : this.message;
  }

  /** The recovery step the server offered, if any. */
  get action(): ErrorAction | null {
    const action = this.detail.action;
    return action && typeof action === "object" ? (action as ErrorAction) : null;
  }

  /** Areas the deployment does cover, for an "outside coverage" message. */
  get covered(): string[] {
    return Array.isArray(this.detail.covered) ? (this.detail.covered as string[]) : [];
  }

  /** The correlation id, for reporting a failure. */
  get requestId(): string | null {
    return typeof this.detail.request_id === "string" ? this.detail.request_id : null;
  }

  /**
   * Flatten the geocoder's per-region street suggestions into one list.
   *
   * A miss returns `{sf: [...], pia: [...]}`; the user wants "did you mean",
   * not a breakdown by region they never asked about.
   */
  suggestions(limit = 4): string[] {
    const raw = this.detail.suggestions;
    if (!raw) return [];
    // The plan endpoint returns a flat list; older shapes grouped by region.
    if (Array.isArray(raw)) return (raw as string[]).slice(0, limit);
    if (typeof raw !== "object") return [];
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

/**
 * `POST /v1/regions` — ask for coverage of somewhere new.
 *
 * Returns immediately with a key to poll; the extraction itself runs in a
 * separate function and takes a minute or two. Idempotent, so calling it twice
 * for the same place joins the existing build rather than starting another.
 */
export function requestRegion(body: { place?: string; lat?: number; lon?: number }): Promise<RegionState> {
  return request<RegionState>("/v1/regions", { method: "POST", body: JSON.stringify(body) });
}

/** `GET /v1/regions/{key}` — one region's build progress. */
export function getRegion(key: string): Promise<RegionState> {
  return request<RegionState>(`/v1/regions/${encodeURIComponent(key)}`);
}

/** `DELETE /v1/regions/{key}` — clear a failed build so it can be retried. */
export function clearRegion(key: string): Promise<{ key: string; cleared: boolean }> {
  return request(`/v1/regions/${encodeURIComponent(key)}`, { method: "DELETE" });
}

export type { ApiErrorBody };
