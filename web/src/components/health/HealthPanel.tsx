import type { Health } from "@/lib/types";
import EnergyCard from "./EnergyCard";
import GuidelineCard from "./GuidelineCard";
import JointLoadCard from "./JointLoadCard";
import StepsCard from "./StepsCard";
import WeightProjectionCard from "./WeightProjectionCard";

/**
 * The health return of one walk: guidelines first, then energy and loading.
 *
 * The two-up grid keys off `@sm` — the width of the enclosing panel — rather
 * than the viewport. The panel is around 380 px on a tablet and the full width
 * of a phone, so a viewport breakpoint would pair these cards up exactly where
 * there is least room for them.
 */
export default function HealthPanel({ health }: { health: Health }) {
  return (
    <>
      <div className="grid gap-3 @sm:grid-cols-2">
        <GuidelineCard guideline={health.guideline_progress} />
        <StepsCard steps={health.steps} />
      </div>
      {health.weight_projection && (
        <WeightProjectionCard projection={health.weight_projection} />
      )}
      <div className="grid gap-3 @sm:grid-cols-2">
        <EnergyCard energy={health.energy} />
        <JointLoadCard jointLoad={health.joint_load} />
      </div>
    </>
  );
}
