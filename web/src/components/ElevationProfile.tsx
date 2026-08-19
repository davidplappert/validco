"use client";

import type { Route } from "@/lib/api";

/**
 * The elevation profile, drawn as inline SVG.
 *
 * A charting library would be several hundred kilobytes to draw one filled
 * area, and this needs no axes, no legend and no interaction beyond reading the
 * shape — so it is about forty lines of path arithmetic instead.
 *
 * The y-axis is deliberately *not* zero-based. A 30 m climb over 2 km is a real
 * hill you will feel, and anchoring the axis at sea level would flatten it into
 * a straight line. The actual range is labelled so the exaggeration is visible
 * rather than misleading.
 */

const W = 560;
const H = 96;
const PAD = 6;

export default function ElevationProfile({ route }: { route: Route }) {
  const points = route.elevation_profile;
  if (points.length < 2) return null;

  const elevations = points.map((p) => p.ele);
  const maxDist = points[points.length - 1].m || 1;
  let lo = Math.min(...elevations);
  let hi = Math.max(...elevations);

  // Guarantee a minimum visual range so a genuinely flat walk renders as a flat
  // line rather than as amplified DEM noise.
  const span = hi - lo;
  if (span < 10) {
    const mid = (hi + lo) / 2;
    lo = mid - 5;
    hi = mid + 5;
  }

  const x = (m: number) => PAD + (m / maxDist) * (W - PAD * 2);
  const y = (ele: number) => H - PAD - ((ele - lo) / (hi - lo)) * (H - PAD * 2);

  const line = points.map((p) => `${x(p.m).toFixed(1)},${y(p.ele).toFixed(1)}`).join(" L ");
  const area = `M ${x(0).toFixed(1)},${H - PAD} L ${line} L ${x(maxDist).toFixed(1)},${H - PAD} Z`;

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-[11px] text-ink-dim">
        <span>Elevation</span>
        <span className="tabular-nums">
          {Math.round(lo * 3.28084)}–{Math.round(hi * 3.28084)} ft
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-24 w-full"
        preserveAspectRatio="none"
        role="img"
        aria-label={`Elevation profile: ${route.effort.ascent_ft} feet of climbing over ${route.effort.distance_mi} miles`}
      >
        <defs>
          <linearGradient id={`elev-${route.id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#a78bfa" stopOpacity="0.45" />
            <stop offset="100%" stopColor="#a78bfa" stopOpacity="0.03" />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#elev-${route.id})`} />
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
