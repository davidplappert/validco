"use client";

import type { Route } from "@/lib/types";
import RouteCard from "./RouteCard";

/** The ranked list of suggestions. */
export default function RouteList({
  routes,
  selectedIndex,
  onSelect,
}: {
  routes: Route[];
  selectedIndex: number;
  onSelect: (index: number) => void;
}) {
  if (routes.length === 0) return null;
  return (
    <div className="flex flex-col gap-2" aria-label="Suggested walks">
      {routes.map((route, index) => (
        <RouteCard
          key={route.id}
          route={route}
          selected={index === selectedIndex}
          onSelect={() => onSelect(index)}
        />
      ))}
    </div>
  );
}
