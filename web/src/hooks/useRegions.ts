"use client";

import { useEffect, useState } from "react";
import { getRegions, loadConfig } from "@/lib/api";
import type { Region } from "@/lib/types";

/**
 * Loads the coverage areas once on mount.
 *
 * Failure is deliberately non-fatal: the region list is used to centre the map
 * and to tell the user where the app works, and the app is still usable without
 * it. So this returns an empty list rather than surfacing an error.
 */
export function useRegions(): { regions: Region[]; loading: boolean } {
  const [regions, setRegions] = useState<Region[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // Kick off config.json immediately. Every API call awaits it, so fetching
    // it during mount rather than on first submit removes a serial round trip
    // from the user's first interaction.
    void loadConfig();
    getRegions()
      .then((response) => {
        if (!cancelled) setRegions(response.regions);
      })
      .catch((error) => console.warn("[stepwise] could not load regions", error))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    // Guard against setting state after unmount in React strict mode, which
    // mounts effects twice in development.
    return () => {
      cancelled = true;
    };
  }, []);

  return { regions, loading };
}
