import ProgressBar from "@/components/health/ProgressBar";
import type { RegionBuildState } from "@/hooks/useRegionBuilder";

/** Human wording for each build stage the API reports. */
const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  resolve: "Locating the area",
  segments: "Downloading streets and paths",
  addresses: "Downloading addresses",
  places: "Downloading places",
  green: "Finding parks and green space",
  graph: "Building the walking network",
  terrain: "Sampling elevation",
  pack: "Packing the data",
  upload: "Storing it",
  ready: "Ready",
};

/**
 * Live progress for an area being extracted from Overture.
 *
 * The wait is one to three minutes, which is far too long for a spinner: with
 * no sense of how much is left, a minute of nothing reads as a hung page. The
 * server reports a real fraction and names the stage it is in, so both are
 * shown, and the bar is determinate rather than an indefinite shimmer.
 *
 * `role="status"` with a polite live region means a screen reader hears the
 * stage changes without them interrupting whatever is being read.
 */
export default function RegionProgress({
  label,
  state,
  progress,
  stage,
  message,
}: {
  label: string;
  state: RegionBuildState;
  /** 0..1, as the API reports it. */
  progress: number;
  stage: string;
  message: string;
}) {
  const percent = Math.round(Math.max(0, Math.min(1, progress)) * 100);
  const stageLabel = STAGE_LABELS[stage] ?? "Working";

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Building coverage"
      className="rounded-lg border border-accent/30 bg-accent/5 p-3 text-xs text-ink-dim"
    >
      <div className="font-medium text-ink">Adding {label}</div>

      <div className="mt-2">
        <ProgressBar percent={percent} label={`Building ${label}`} />
      </div>

      <div className="mt-2 flex items-baseline justify-between gap-2">
        <span>{message || stageLabel}</span>
        <span className="shrink-0 tabular-nums text-ink-dim/70">{percent}%</span>
      </div>

      {state === "requesting" ? (
        <p className="mt-2 text-ink-dim/70">Starting the build…</p>
      ) : (
        // Say what the wait buys, so it reads as a one-off cost rather than as
        // how slow this app is. It is also true: the built area is cached.
        <p className="mt-2 text-ink-dim/70">
          This runs once for this area and takes a minute or two. Next time it loads instantly.
        </p>
      )}
    </div>
  );
}
