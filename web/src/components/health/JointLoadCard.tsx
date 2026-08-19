import type { JointLoadReport } from "@/lib/types";
import { thousands } from "@/lib/format";

/**
 * Peak knee loading.
 *
 * Shown because for the heaviest users it is the thing most likely to end a
 * walking habit, and because the four-pounds-per-pound ratio is motivating in a
 * way calorie counts are not.
 */
export default function JointLoadCard({ jointLoad }: { jointLoad: JointLoadReport }) {
  return (
    <div className="rounded-lg bg-ground/60 p-3 text-xs text-ink-dim">
      <div className="mb-1 font-medium text-ink">Joint loading</div>
      <div>
        Peak knee force ≈ {jointLoad.peak_knee_force_bw}× body weight (
        {thousands(jointLoad.peak_knee_force_lb)} lb) per step.
      </div>
      <div className="mt-1">{jointLoad.note}</div>
    </div>
  );
}
