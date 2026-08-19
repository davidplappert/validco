import type { ElevationPoint } from "@/lib/types";

/**
 * The elevation profile, as inline SVG.
 *
 * A charting library would be several hundred kilobytes to draw one filled
 * area, and this needs no axes, no legend and no interaction beyond reading the
 * shape — so it is forty lines of path arithmetic instead.
 *
 * The y-axis is deliberately not zero-based. A 30 m climb over 2 km is a real
 * hill you will feel, and anchoring at sea level would flatten it to a straight
 * line. The actual range is labelled, so the exaggeration is visible rather than
 * misleading.
 */

const WIDTH = 560;
const HEIGHT = 96;
const PADDING = 6;

/** Minimum vertical range in metres, so a flat walk does not render DEM noise. */
const MIN_RANGE_M = 10;

export default function ElevationProfile({
  points,
  ascentFt,
  distanceMi,
  gradientId = "elevation",
}: {
  points: ElevationPoint[];
  ascentFt: number;
  distanceMi: number;
  gradientId?: string;
}) {
  if (points.length < 2) return null;

  const elevations = points.map((point) => point.ele);
  const maxDistance = points[points.length - 1].m || 1;
  let low = Math.min(...elevations);
  let high = Math.max(...elevations);

  if (high - low < MIN_RANGE_M) {
    const middle = (high + low) / 2;
    low = middle - MIN_RANGE_M / 2;
    high = middle + MIN_RANGE_M / 2;
  }

  const x = (metres: number) => PADDING + (metres / maxDistance) * (WIDTH - PADDING * 2);
  const y = (elevation: number) =>
    HEIGHT - PADDING - ((elevation - low) / (high - low)) * (HEIGHT - PADDING * 2);

  const line = points.map((p) => `${x(p.m).toFixed(1)},${y(p.ele).toFixed(1)}`).join(" L ");
  const area = `M ${x(0).toFixed(1)},${HEIGHT - PADDING} L ${line} L ${x(maxDistance).toFixed(1)},${HEIGHT - PADDING} Z`;

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-[11px] text-ink-dim">
        <span>Elevation</span>
        <span className="tabular-nums">
          {Math.round(low * 3.28084)}–{Math.round(high * 3.28084)} ft
        </span>
      </div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-24 w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={`Elevation profile: ${ascentFt} feet of climbing over ${distanceMi} miles`}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#a78bfa" stopOpacity="0.45" />
            <stop offset="100%" stopColor="#a78bfa" stopOpacity="0.03" />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#${gradientId})`} />
        <path
          d={`M ${line}`}
          fill="none"
          stroke="#a78bfa"
          strokeWidth="1.75"
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}
