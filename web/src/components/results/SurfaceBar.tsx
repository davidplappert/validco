import { surfaceColour } from "@/components/map/surfaceColours";

/**
 * The surface mix, as a stacked bar plus a legend.
 *
 * Uses the same colours as the map line, so the bar and the drawn route are
 * obviously the same information in two forms. This is the "walking on a road
 * or on a path" answer, which is half of what the product is for.
 */
export default function SurfaceBar({
  breakdown,
  labels,
}: {
  breakdown: Record<string, number>;
  labels: Record<string, string>;
}) {
  const surfaces = Object.entries(breakdown).sort((a, b) => b[1] - a[1]);
  if (surfaces.length === 0) return null;

  return (
    <div>
      <div
        className="mb-1 flex h-2 w-full overflow-hidden rounded-full bg-ground"
        role="img"
        aria-label={surfaces.map(([s, p]) => `${labels[s] ?? s} ${p}%`).join(", ")}
      >
        {surfaces.map(([surface, percent]) => (
          <div
            key={surface}
            style={{ width: `${percent}%`, backgroundColor: surfaceColour(surface) }}
            title={`${labels[surface] ?? surface}: ${percent}%`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-dim">
        {surfaces.map(([surface, percent]) => (
          <span key={surface} className="inline-flex items-center gap-1">
            <span
              aria-hidden="true"
              className="inline-block h-2 w-2 rounded-full"
              style={{ backgroundColor: surfaceColour(surface) }}
            />
            {labels[surface] ?? surface} {percent}%
          </span>
        ))}
      </div>
    </div>
  );
}
