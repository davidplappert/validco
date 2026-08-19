import type { Route } from "@/lib/types";
import FitBadge from "./FitBadge";

/**
 * The title line of a route card.
 *
 * Names the destination when there is one, because "out to Alta Plaza Park" is
 * a walk someone wants to take and "loop 2" is not.
 */
export default function RouteCardHeader({ route }: { route: Route }) {
  const title = route.destination
    ? `Out to ${route.destination.name}`
    : "Neighbourhood loop";
  const shape = route.shape === "loop" ? "Loop" : "Out and back";

  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="truncate font-medium">{title}</div>
        <div className="truncate text-xs text-ink-dim">
          {shape}
          {route.streets.length > 0 && ` · via ${route.streets.slice(0, 3).join(", ")}`}
        </div>
      </div>
      <FitBadge score={route.suitability.score} />
    </div>
  );
}
