"use client";

import { useMemo } from "react";
import dynamic from "next/dynamic";
import PlanForm from "@/components/form/PlanForm";
import ErrorPanel from "@/components/feedback/ErrorPanel";
import AppHeader from "@/components/layout/AppHeader";
import Attribution from "@/components/layout/Attribution";
import Panel from "@/components/layout/Panel";
import MapLegend from "@/components/map/MapLegend";
import ResultsPanel from "@/components/results/ResultsPanel";
import { usePlanner } from "@/hooks/usePlanner";
import { useRegions } from "@/hooks/useRegions";

// MapLibre touches `window` on import, so it must stay out of the static
// prerender that `next build` performs at export time.
const MapView = dynamic(() => import("@/components/map/MapView"), {
  ssr: false,
  loading: () => <div className="absolute inset-0 bg-ground" />,
});

/** Fallback map centre when no regions have loaded and nothing is planned. */
const FALLBACK_CENTER: [number, number] = [37.7749, -122.4194];

/**
 * The application shell.
 *
 * Deliberately thin: two hooks for state, a handful of components for
 * presentation. Everything with logic in it — the request lifecycle, the form,
 * each result card — lives in its own tested unit.
 */
export default function Home() {
  const { regions } = useRegions();
  const { result, selectedIndex, busy, error, submit, select } = usePlanner();

  const center = useMemo<[number, number]>(() => {
    if (result) return [result.origin.lat, result.origin.lon];
    return regions[0]?.center ?? FALLBACK_CENTER;
  }, [result, regions]);

  const activeRoute = result?.routes[selectedIndex] ?? null;

  return (
    <main className="relative h-dvh w-full overflow-hidden">
      <MapView
        route={activeRoute}
        origin={
          result ? { lat: result.origin.snapped_lat, lon: result.origin.snapped_lon } : null
        }
        center={center}
      />
      <MapLegend />

      <Panel>
        <AppHeader />
        <PlanForm busy={busy} onSubmit={submit} />
        {error && <ErrorPanel error={error} regions={regions} />}
        {result && (
          <ResultsPanel
            result={result}
            selectedIndex={selectedIndex}
            onSelect={select}
          />
        )}
        <Attribution />
      </Panel>
    </main>
  );
}
