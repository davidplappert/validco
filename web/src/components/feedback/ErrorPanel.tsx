import type { PlannerError } from "@/hooks/usePlanner";
import type { KnownRegion } from "@/hooks/useRegionBuilder";
import type { Region } from "@/lib/types";

/**
 * A failed request, with whatever the API offered to make it actionable.
 *
 * The coverage list is shown alongside the error because the most common
 * failure by far is an address outside the built-in regions, and naming them
 * turns a dead end into an obvious next step. Areas this browser has added on
 * demand are listed separately: they are not in the deployment's own coverage
 * list, but they are places the user can plan from right now, and forgetting to
 * mention them would make the app look like it lost their work.
 */
export default function ErrorPanel({
  error,
  regions,
  added = [],
}: {
  error: PlannerError;
  regions: Region[];
  added?: KnownRegion[];
}) {
  return (
    <div
      role="alert"
      // Named because Next.js injects its own role="alert" route announcer, so
      // "the alert" is ambiguous to both tests and screen readers without it.
      aria-label="Planning error"
      className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200"
    >
      <div className="font-medium">{error.message}</div>
      {error.hint && <div className="mt-1 text-rose-300/80">{error.hint}</div>}
      {regions.length > 0 && (
        <div className="mt-2 text-rose-300/70">
          Covered areas: {regions.map((region) => region.label).join(" · ")}
        </div>
      )}
      {added.length > 0 && (
        <div className="mt-1 text-rose-300/70">
          Areas you&rsquo;ve added: {added.map((region) => region.label).join(" · ")}
        </div>
      )}
    </div>
  );
}
