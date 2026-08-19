import { clampPercent } from "@/lib/format";

/**
 * A labelled progress bar for guideline completion.
 *
 * Carries full ARIA meter semantics because the visual bar is the only
 * indication of progress, and the underlying percentage is genuinely the
 * information — not decoration.
 */
export default function ProgressBar({
  percent,
  label,
}: {
  percent: number;
  label: string;
}) {
  const width = clampPercent(percent);
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuenow={Math.round(width)}
      aria-valuemin={0}
      aria-valuemax={100}
      className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2"
    >
      <div className="h-full bg-accent transition-[width] duration-500" style={{ width: `${width}%` }} />
    </div>
  );
}
