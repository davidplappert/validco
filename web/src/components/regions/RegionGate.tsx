"use client";

import ErrorPanel from "@/components/feedback/ErrorPanel";
import AddRegionPrompt from "./AddRegionPrompt";
import RegionProgress from "./RegionProgress";
import type { PlannerError } from "@/hooks/usePlanner";
import type { RegionBuilder } from "@/hooks/useRegionBuilder";
import type { ErrorAction, Region } from "@/lib/types";

/** Build states where something is actually in flight. */
const IN_FLIGHT = new Set(["requesting", "pending", "building"]);

/**
 * Picks the one piece of feedback a failed plan deserves.
 *
 * Three of the API's error codes are about coverage rather than about the
 * request, and each wants a different answer: `region_not_covered` is an offer
 * to build, `region_building` means the wait has already started somewhere
 * else, and `region_build_failed` needs the failed record cleared before it can
 * be attempted again. Everything else is an ordinary error.
 *
 * Doing the choosing here rather than in the page keeps the page composition
 * only, and makes the precedence — a build in flight outranks whatever error
 * started it — a single readable list instead of nested conditionals in JSX.
 */
export default function RegionGate({
  error,
  builder,
  regions,
  onAddRegion,
  onRetryRegion,
}: {
  error: PlannerError | null;
  builder: RegionBuilder;
  regions: Region[];
  onAddRegion: (action: ErrorAction) => void;
  onRetryRegion: (action: ErrorAction | null) => void;
}) {
  // A build in progress replaces the error that triggered it. The error is
  // stale by then — it described a moment before the user did something about
  // it — and showing both would read as though the build had already failed.
  if (IN_FLIGHT.has(builder.state)) {
    return (
      <RegionProgress
        label={builder.label ?? "this area"}
        state={builder.state}
        progress={builder.progress}
        stage={builder.stage}
        message={builder.message}
      />
    );
  }

  if (builder.state === "failed") {
    return (
      <AddRegionPrompt
        title="We couldn't prepare that area"
        detail={builder.error ?? "The build did not finish."}
        actionLabel="Try again"
        onAccept={() => onRetryRegion(null)}
      />
    );
  }

  if (!error) return null;

  if (error.code === "region_not_covered" && error.action) {
    const action = error.action;
    return (
      <AddRegionPrompt
        title={error.title ?? "We don't have walking data for that area yet"}
        detail={error.hint ?? error.message}
        actionLabel={action.label || "Add this area"}
        onAccept={() => onAddRegion(action)}
      />
    );
  }

  if (error.code === "region_build_failed") {
    return (
      <AddRegionPrompt
        title={error.title ?? "We couldn't prepare that area"}
        detail={error.hint ?? error.message}
        actionLabel={error.action?.label || "Try again"}
        onAccept={() => onRetryRegion(error.action ?? null)}
      />
    );
  }

  // `region_building` renders nothing here: the page starts watching it as soon
  // as the error arrives, so the progress branch above takes over on the next
  // render and an error panel would only flash in between.
  if (error.code === "region_building") return null;

  return <ErrorPanel error={error} regions={regions} added={builder.known} />;
}
