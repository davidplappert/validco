"use client";

import type { Route } from "@/lib/types";
import RouteCardHeader from "./RouteCardHeader";
import RouteDetail from "./RouteDetail";
import RouteStats from "./RouteStats";
import SurfaceBar from "./SurfaceBar";

/**
 * One suggested walk, collapsed or expanded.
 *
 * The whole card is the control rather than a nested button, so the click
 * target matches what the user perceives as clickable. `aria-expanded` carries
 * the state, since the card both selects the route on the map and reveals its
 * detail.
 */
export default function RouteCard({
  route,
  selected,
  onSelect,
}: {
  route: Route;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      aria-expanded={selected}
      className={`animate-fade-up w-full rounded-xl border p-4 text-left transition ${
        selected
          ? "border-accent/70 bg-surface-2 shadow-lg shadow-black/30"
          : "border-line bg-surface hover:border-accent/40 hover:bg-surface-2"
      }`}
    >
      <div className="mb-3">
        <RouteCardHeader route={route} />
      </div>
      <div className="mb-3">
        <RouteStats effort={route.effort} />
      </div>
      <SurfaceBar
        breakdown={route.surface_breakdown_pct}
        labels={route.surface_labels}
      />
      {selected && <RouteDetail route={route} />}
    </button>
  );
}
