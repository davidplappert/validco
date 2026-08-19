import type { Effort } from "@/lib/types";
import { feet, kcal, miles, minutes, thousands } from "@/lib/format";
import Stat from "./Stat";

/**
 * The four headline figures for a route.
 *
 * Ordered as the argument the product makes: time and distance first (what was
 * asked for), then climb (what it will feel like), then energy (why bother).
 *
 * Four across needs roughly 80 px a column before "263 kcal" starts truncating,
 * which a phone-width card cannot give. `@sm` measures the panel rather than
 * the viewport, so the row folds to two-by-two only where it actually has to.
 */
export default function RouteStats({ effort }: { effort: Effort }) {
  return (
    <div className="grid grid-cols-2 gap-3 @sm:grid-cols-4">
      <Stat label="Time" value={minutes(effort.duration_min)} sub={`${effort.avg_speed_mph} mph`} />
      <Stat
        label="Distance"
        value={miles(effort.distance_mi)}
        sub={`${thousands(effort.steps)} steps`}
      />
      <Stat label="Climb" value={feet(effort.ascent_ft)} sub={`peak ${effort.peak_grade_pct}%`} />
      <Stat
        label="Energy"
        value={kcal(effort.kcal_gross)}
        sub={`${effort.mets} MET · ${effort.intensity}`}
      />
    </div>
  );
}
