import type { GuidelineProgress } from "@/lib/types";
import ProgressBar from "./ProgressBar";

/** Progress toward the WHO weekly moderate-activity target. */
export default function GuidelineCard({ guideline }: { guideline: GuidelineProgress }) {
  return (
    <div className="rounded-lg bg-ground/60 p-3">
      <div className="mb-1 text-[11px] uppercase tracking-wide text-ink-dim">
        Weekly activity target
      </div>
      <div className="mb-2">
        <ProgressBar
          percent={guideline.pct_of_weekly_target}
          label="Progress toward the weekly moderate-activity target"
        />
      </div>
      <div className="text-xs text-ink-dim">
        {guideline.pct_of_weekly_target}% of the WHO{" "}
        {guideline.who_weekly_moderate_min} min/week of moderate activity
        {/* Saying so explicitly matters: claiming guideline credit for a walk
            below the moderate threshold would misrepresent the guideline. */}
        {!guideline.counts_as_moderate && " (this pace counts as light)"}
      </div>
    </div>
  );
}
