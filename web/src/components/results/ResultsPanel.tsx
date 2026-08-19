"use client";

import type { PlanResponse } from "@/lib/types";
import Caveats from "@/components/health/Caveats";
import OriginSummary from "./OriginSummary";
import RouteList from "./RouteList";

/** Everything shown after a successful plan. */
export default function ResultsPanel({
  result,
  selectedIndex,
  onSelect,
}: {
  result: PlanResponse;
  selectedIndex: number;
  onSelect: (index: number) => void;
}) {
  const selected = result.routes[selectedIndex];
  return (
    <>
      <OriginSummary
        origin={result.origin}
        profile={result.profile}
        routeCount={result.routes.length}
        planMs={result.timing_ms.plan}
      />
      <RouteList routes={result.routes} selectedIndex={selectedIndex} onSelect={onSelect} />
      {selected && <Caveats caveats={selected.health.caveats} />}
    </>
  );
}
