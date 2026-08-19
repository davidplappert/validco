"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import dynamic from "next/dynamic";
import PlanForm, { DEFAULT_PLAN_REQUEST } from "@/components/form/PlanForm";
import AppHeader from "@/components/layout/AppHeader";
import Attribution from "@/components/layout/Attribution";
import Panel from "@/components/layout/Panel";
import UpdatingOverlay from "@/components/feedback/UpdatingOverlay";
import MapLegend from "@/components/map/MapLegend";
import RegionGate from "@/components/regions/RegionGate";
import ResultsPanel from "@/components/results/ResultsPanel";
import { usePlanner } from "@/hooks/usePlanner";
import { useRegionBuilder } from "@/hooks/useRegionBuilder";
import { useRegions } from "@/hooks/useRegions";
import type { PlanRequest } from "@/lib/api";
import type { ErrorAction } from "@/lib/types";

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
  // Plan the default address immediately, so the app opens showing a drawn
  // route and real health figures rather than an empty form. An empty first
  // screen asks the user to do work before they can tell whether the product
  // is worth their time.
  const { result, selectedIndex, busy, error, priming, submit, select } = usePlanner({
    autoRun: DEFAULT_PLAN_REQUEST,
  });

  // The request that produced the current state, kept so a freshly-built area
  // can be planned without the user retyping anything.
  const lastRequest = useRef<PlanRequest>(DEFAULT_PLAN_REQUEST);

  const builder = useRegionBuilder({
    // The wait only means something if the walk arrives at the end of it.
    onReady: () => void submit(lastRequest.current),
  });
  const { request: requestRegion, retry: retryRegion, cancel: cancelRegion } = builder;

  const handleSubmit = useCallback(
    (request: PlanRequest) => {
      lastRequest.current = request;
      // A new address abandons whatever the previous one was waiting on. If it
      // is the same area, the plan comes straight back as `region_building` and
      // the effect below picks the watch up again.
      cancelRegion();
      void submit(request);
    },
    [cancelRegion, submit],
  );

  const handleAddRegion = useCallback(
    (action: ErrorAction) => {
      requestRegion({ place: action.place, lat: action.lat, lon: action.lon });
    },
    [requestRegion],
  );

  const handleRetryRegion = useCallback(
    (action: ErrorAction | null) => {
      // `retry_region` names the key but not the place, so the address the user
      // typed is passed along as the thing to re-request once it is cleared.
      retryRegion(
        action
          ? {
              key: action.key,
              place: action.place ?? lastRequest.current.address,
            }
          : undefined,
      );
    },
    [retryRegion],
  );

  // Someone else already started this build, so join it rather than starting
  // another. `error` is a fresh object per failure, so this fires once each.
  useEffect(() => {
    if (error?.code !== "region_building" || !error.action?.key) return;
    requestRegion({ key: error.action.key });
  }, [error, requestRegion]);

  const center = useMemo<[number, number]>(() => {
    if (result) return [result.origin.lat, result.origin.lon];
    return regions[0]?.center ?? FALLBACK_CENTER;
  }, [result, regions]);

  const activeRoute = result?.routes[selectedIndex] ?? null;

  return (
    // Below `sm` the shell is a real two-row flex column, so the panel takes
    // space from the map instead of covering it. From `sm` up the map goes back
    // to filling the viewport with the panel floating over its right edge.
    <main className="relative flex h-dvh w-full flex-col overflow-hidden sm:block">
      {/*
        The map's own pane. It exists so MapView keeps its `absolute inset-0`
        (see the layer note in globals.css — that positioning is load-bearing)
        while still being sized by the flex row on a phone. Keeping the legend
        inside it also means the legend is positioned against the map rather
        than the viewport, so it cannot drift under the panel.
      */}
      <div className="relative h-[38%] shrink-0 sm:absolute sm:inset-0 sm:h-full">
        <MapView
          route={activeRoute}
          origin={
            result
              ? {
                  lat: result.origin.snapped_lat,
                  lon: result.origin.snapped_lon,
                }
              : null
          }
          center={center}
        />
        <MapLegend />
      </div>

      <Panel>
        <AppHeader />
        <PlanForm busy={busy} onSubmit={handleSubmit} />
        <RegionGate
          error={error}
          builder={builder}
          regions={regions}
          onAddRegion={handleAddRegion}
          onRetryRegion={handleRetryRegion}
        />
        {builder.state === "ready" && busy && (
          <p className="text-xs text-ink-dim" role="status">
            {builder.label ?? "That area"} is ready — planning your walk…
          </p>
        )}
        {priming && !result && !error && (
          <p className="text-xs text-ink-dim" role="status">
            Planning a first walk so you can see what this does…
          </p>
        )}
        {result && <ResultsPanel result={result} selectedIndex={selectedIndex} onSelect={select} />}
        <Attribution />
      </Panel>

      {/*
        Last child of <main> so it sits above both the map and the panel. It is
        driven by `busy` alone, never by the debounce's pending flag: during the
        wait after a keystroke nothing is actually being computed, and dimming
        the screen while the user is still thinking would be a lie about what
        the app is doing.
      */}
      <UpdatingOverlay active={busy} />
    </main>
  );
}
