/**
 * Client for `GET /v1/suggest` — address completions as the user types.
 *
 * Lives beside `api.ts` rather than inside it for one reason that matters at
 * this call rate: `api.ts`'s shared `request()` helper sets a JSON
 * `content-type` on every call, and a `content-type: application/json` header
 * turns a plain GET into a CORS *preflighted* request. That is a second round
 * trip per keystroke to a cross-origin execute-api host, for a body that does
 * not exist. Sending no custom headers keeps this a simple request. It also
 * needs an `AbortSignal`, which `request()` does not thread through.
 *
 * It shares `loadConfig()` with `api.ts`, so there is still exactly one place
 * that knows where the API is.
 */

import { ApiError, loadConfig } from "./api";

/**
 * One completion.
 *
 * Matches the `suggestions[]` item in `openapi.yaml`. `lat`/`lon` are nullable:
 * a street-level suggestion is positioned at the midpoint of the street, and a
 * street whose rows carry no usable coordinate reports null rather than lying.
 * When they are present the caller can plan straight from them and skip the
 * geocode round trip entirely.
 */
export interface AddressSuggestion {
  kind: "street" | "address";
  /** What to show in the dropdown, e.g. `1100 CALIFORNIA ST, 94108`. */
  label: string;
  /** What to put in the input when chosen. */
  value: string;
  /** The region key the suggestion came from, e.g. `sf`. */
  region: string;
  lat: number | null;
  lon: number | null;
}

/** The full `GET /v1/suggest` body. */
export interface SuggestResponse {
  query: string;
  suggestions: AddressSuggestion[];
  attribution?: string;
}

export interface SuggestOptions {
  /** 1–10; the API defaults to 8. */
  limit?: number;
  /** Restrict to one region. Omit to search everything already resident. */
  region?: string;
  signal?: AbortSignal;
}

/**
 * Fetch completions for a partial address.
 *
 * The endpoint always answers 200 — a query too short or too long returns an
 * empty list rather than a 4xx, because an error mid-word is not something the
 * user can act on. This function therefore only rejects on transport or
 * server failure, and callers are expected to treat that as "no dropdown".
 */
export async function suggestAddresses(
  query: string,
  options: SuggestOptions = {},
): Promise<AddressSuggestion[]> {
  const config = await loadConfig();
  const params = new URLSearchParams({ q: query });
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.region) params.set("region", options.region);

  const response = await fetch(`${config.apiBaseUrl}/v1/suggest?${params.toString()}`, {
    signal: options.signal,
  });
  if (!response.ok) {
    throw new ApiError(`suggest failed (${response.status})`, response.status);
  }

  const body = (await response.json()) as SuggestResponse;
  return Array.isArray(body?.suggestions) ? body.suggestions : [];
}
