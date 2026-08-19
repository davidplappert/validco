import type { StepProgress } from "@/lib/types";
import { thousands } from "@/lib/format";
import ProgressBar from "./ProgressBar";

/** Progress toward the daily step target from the 2025 Lancet meta-analysis. */
export default function StepsCard({ steps }: { steps: StepProgress }) {
  return (
    <div className="rounded-lg bg-ground/60 p-3">
      <div className="mb-1 text-[11px] uppercase tracking-wide text-ink-dim">Daily steps</div>
      <div className="mb-2">
        <ProgressBar
          percent={steps.pct_of_daily_target}
          label="Progress toward the daily step target"
        />
      </div>
      <div className="text-xs text-ink-dim">
        {thousands(steps.walk_steps)} steps — {steps.pct_of_daily_target}% of a{" "}
        {thousands(steps.daily_target)}-step day
      </div>
    </div>
  );
}
