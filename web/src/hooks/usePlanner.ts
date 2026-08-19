"use client";

import { useCallback, useState } from "react";
import { ApiError, planWalk, type PlanRequest } from "@/lib/api";
import type { PlanResponse } from "@/lib/types";

export interface PlannerError {
  message: string;
  hint?: string;
}

export interface PlannerState {
  result: PlanResponse | null;
  selectedIndex: number;
  busy: boolean;
  error: PlannerError | null;
  submit: (request: PlanRequest) => Promise<void>;
  select: (index: number) => void;
  reset: () => void;
}

/**
 * Owns the request lifecycle for planning a walk.
 *
 * Extracted from the page so the state machine — busy, error, result, which
 * route is selected — can be tested without rendering a map or a form.
 */
export function usePlanner(): PlannerState {
  const [result, setResult] = useState<PlanResponse | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlannerError | null>(null);

  const submit = useCallback(async (request: PlanRequest) => {
    setBusy(true);
    setError(null);
    try {
      const response = await planWalk(request);
      setResult(response);
      setSelectedIndex(0);
    } catch (caught) {
      // The API returns street suggestions on a geocoding miss, which is far
      // more actionable than "not found" — surface them as the hint.
      if (caught instanceof ApiError) {
        const suggestions = caught.suggestions();
        setError({
          message: caught.message,
          hint: suggestions.length ? `Did you mean: ${suggestions.join(", ")}?` : undefined,
        });
      } else {
        setError({
          message: caught instanceof Error ? caught.message : "Something went wrong",
        });
      }
      setResult(null);
    } finally {
      setBusy(false);
    }
  }, []);

  const select = useCallback((index: number) => setSelectedIndex(index), []);

  const reset = useCallback(() => {
    setResult(null);
    setError(null);
    setSelectedIndex(0);
  }, []);

  return { result, selectedIndex, busy, error, submit, select, reset };
}
