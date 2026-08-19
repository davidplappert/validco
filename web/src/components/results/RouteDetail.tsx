import type { Route } from "@/lib/types";
import ElevationProfile from "@/components/chart/ElevationProfile";
import HealthPanel from "@/components/health/HealthPanel";
import SuitabilityNotes from "@/components/health/SuitabilityNotes";
import FeatureTags from "./FeatureTags";

/**
 * The expanded body of a selected route card.
 *
 * Split out so the collapsed card stays cheap: with four routes on screen, only
 * the selected one renders an SVG chart and four health cards.
 */
export default function RouteDetail({ route }: { route: Route }) {
  return (
    <div className="mt-4 space-y-4 border-t border-line pt-4">
      <ElevationProfile
        points={route.elevation_profile}
        ascentFt={route.effort.ascent_ft}
        distanceMi={route.effort.distance_mi}
        gradientId={`elevation-${route.id}`}
      />
      <HealthPanel health={route.health} />
      <SuitabilityNotes suitability={route.suitability} />
      <FeatureTags features={route.features} />
    </div>
  );
}
