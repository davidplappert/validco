import { SURFACE_COLOURS } from "./surfaceColours";

const LABELS: Record<string, string> = {
  path: "Walking path",
  sidewalk: "Sidewalk",
  crossing: "Crossing",
  road: "Road",
};

/**
 * Key for the route's surface colours.
 *
 * Sits over the map rather than in the panel because it explains the map, and
 * a legend two feet from the thing it describes is not a legend.
 */
export default function MapLegend() {
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 z-10 rounded-lg border border-line bg-ground/85 px-2.5 py-2 backdrop-blur-sm">
      <ul className="flex flex-col gap-1">
        {Object.entries(SURFACE_COLOURS).map(([surface, colour]) => (
          <li key={surface} className="flex items-center gap-1.5 text-[10px] text-ink-dim">
            <span
              aria-hidden="true"
              className="inline-block h-1.5 w-4 rounded-full"
              style={{ backgroundColor: colour }}
            />
            {LABELS[surface] ?? surface}
          </li>
        ))}
      </ul>
    </div>
  );
}
