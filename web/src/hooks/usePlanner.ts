"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, planWalk, type PlanRequest } from "@/lib/api";
import type { ErrorAction, PlanResponse } from "@/lib/types";

/**
 * A failed plan, keeping what the server said about how to recover.
 *
 * `message` and `hint` are what gets rendered; `code` and `action` are what the
 * page branches on. Branching on the machine code is what turns "we don't cover
 * that area" into an offer to build it, so the structured fields have to survive
 * the trip out of the hook rather than being flattened into a sentence here.
 */
export interface PlannerError {
  message: string;
  hint?: string;
  /** The API's machine-readable failure kind, e.g. `region_not_covered`. */
  code?: string;
  /** A heading the server considers safe to show. */
  title?: string;
  /** The recovery step the server offered, if any. */
  action?: ErrorAction | null;
}

export interface PlannerState {
  result: PlanResponse | null;
  selectedIndex: number;
  busy: boolean;
  error: PlannerError | null;
  /** True while the automatic first plan is running, as opposed to a user's. */
  priming: boolean;
  submit: (request: PlanRequest) => Promise<void>;
  select: (index: number) => void;
  reset: () => void;
}

export interface PlannerOptions {
  /**
   * A request to run once on mount, so the app opens with a real map and real
   * results rather than an empty form.
   *
   * An empty first screen makes the user do work before seeing whether the app
   * is worth their time. Planning the default address immediately shows the
   * product working — the route drawn, the health figures filled in — and the
   * form above it is already populated for them to change.
   */
  autoRun?: PlanRequest;
}

/**
 * Owns the request lifecycle for planning a walk.
 *
 * Extracted from the page so the state machine — busy, error, result, which
 * route is selected — can be tested without rendering a map or a form.
 */
export function usePlanner(options: PlannerOptions = {}): PlannerState {
  const [result, setResult] = useState<PlanResponse | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlannerError | null>(null);
  const [priming, setPriming] = useState(Boolean(options.autoRun));

  // Which submission is current. Once the form auto-updates as you type, two
  // plans can be in flight at once and they do not necessarily land in order —
  // a 40-minute request against a dense city can easily outlast the 20-minute
  // one typed after it. Without this guard the stale response wins simply by
  // arriving last, and the map shows a walk for a body weight the user has
  // already changed. Every state write below is gated on still being current.
  const sequence = useRef(0);

  const submit = useCallback(async (request: PlanRequest) => {
    const id = ++sequence.current;
    setBusy(true);
    setError(null);
    try {
      const response = await planWalk(request);
      if (id !== sequence.current) return;
      setResult(response);
      setSelectedIndex(0);
    } catch (caught) {
      if (id !== sequence.current) return;
      // The API returns street suggestions on a geocoding miss, which is far
      // more actionable than "not found" — surface them as the hint.
      if (caught instanceof ApiError) {
        const suggestions = caught.suggestions();
        setError({
          message: caught.message,
          hint: suggestions.length
            ? `Did you mean: ${suggestions.join(", ")}?`
            : caught.userDetail !== caught.message
              ? caught.userDetail
              : undefined,
          code: caught.code,
          title: caught.title,
          action: caught.action,
        });
      } else if (caught instanceof Error && /config\.json/.test(caught.message)) {
        // The runtime config is written into the site bucket by CDK. If it is
        // missing the app has no API to talk to at all — which is a deployment
        // problem, not something the user did, and should not be reported to
        // them in the language of a 404.
        setError({
          message: "This deployment is not fully configured yet",
          hint: "The app could not find its API settings. Try again shortly.",
        });
      } else {
        setError({
          message: caught instanceof Error ? caught.message : "Something went wrong",
        });
      }
      // Deliberately *not* clearing the result. While the form updates as you
      // type, most failures are transient — a half-typed address that does not
      // geocode yet — and blanking the map on each one makes the app flicker
      // between working and broken. The error is shown above a result that is
      // merely stale, which is the more honest of the two states.
    } finally {
      if (id === sequence.current) setBusy(false);
    }
  }, []);

  const select = useCallback((index: number) => setSelectedIndex(index), []);

  const reset = useCallback(() => {
    // Abandons any in-flight submission too, so a late response cannot
    // repopulate what the caller just cleared.
    sequence.current += 1;
    setResult(null);
    setError(null);
    setSelectedIndex(0);
  }, []);

  // Run the opening plan exactly once. The ref guard matters: React's strict
  // mode mounts effects twice in development, and without it the app would
  // fire two identical requests on every load.
  const autoRun = options.autoRun;
  const primed = useRef(false);
  useEffect(() => {
    if (!autoRun || primed.current) return;
    primed.current = true;
    void submit(autoRun).finally(() => setPriming(false));
  }, [autoRun, submit]);

  return { result, selectedIndex, busy, error, priming, submit, select, reset };
}
