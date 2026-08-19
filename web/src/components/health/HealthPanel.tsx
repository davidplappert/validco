import type { Health } from "@/lib/types";
import EnergyCard from "./EnergyCard";
import GuidelineCard from "./GuidelineCard";
import JointLoadCard from "./JointLoadCard";
import StepsCard from "./StepsCard";

/** The health return of one walk: guidelines first, then energy and loading. */
export default function HealthPanel({ health }: { health: Health }) {
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        <GuidelineCard guideline={health.guideline_progress} />
        <StepsCard steps={health.steps} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <EnergyCard energy={health.energy} />
        <JointLoadCard jointLoad={health.joint_load} />
      </div>
    </>
  );
}
