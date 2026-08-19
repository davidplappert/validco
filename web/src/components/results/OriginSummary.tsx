import type { DerivedProfile, Origin } from "@/lib/types";

/**
 * What the API made of the request.
 *
 * Shows the derived physiology and the snap distance rather than hiding them.
 * Two reasons: it lets the user sanity-check that their inputs landed, and the
 * snap distance is the app admitting it put them on the nearest mapped path
 * rather than on their doorstep.
 */
export default function OriginSummary({
  origin,
  profile,
  routeCount,
  planMs,
}: {
  origin: Origin;
  profile: DerivedProfile;
  routeCount: number;
  planMs: number;
}) {
  return (
    <div
      // Named so it can be addressed directly. The resolved address also
      // appears in each route's "via ..." list and, since the address field
      // became a combobox, in the suggestion dropdown — so an unscoped search
      // for that text matches several elements at once.
      role="group"
      aria-label="Start point"
      className="rounded-lg border border-line bg-surface p-3 text-xs text-ink-dim"
    >
      <div className="text-ink">{origin.label ?? "Your start"}</div>
      <div className="mt-1">
        BMI {profile.bmi} ({profile.bmi_class}) · comfortable pace {profile.baseline_speed_mph} mph
        · resting {profile.rmr_kcal_day} kcal/day
      </div>
      <div className="mt-1">
        Snapped {origin.snap_distance_m} m to the walking network · {routeCount} routes in{" "}
        {Math.round(planMs)} ms
      </div>
      {profile.height_assumed && (
        <div className="mt-1 text-amber-300/80">
          Height was assumed — enter it for a better estimate.
        </div>
      )}
    </div>
  );
}
