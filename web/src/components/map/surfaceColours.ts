/**
 * One colour per surface class, shared by the map line, the surface bar and the
 * legend.
 *
 * Defined once because the whole point of colouring the route is that the
 * legend, the bar and the line agree — three copies of these hex values would
 * eventually disagree.
 */
export const SURFACE_COLOURS: Record<string, string> = {
  path: "#4ade80",
  sidewalk: "#60a5fa",
  crossing: "#fbbf24",
  road: "#f87171",
};

/** Fallback for a surface class the frontend does not know about. */
export const UNKNOWN_SURFACE_COLOUR = "#94a3b8";

/** Colour for a surface class, falling back for anything unrecognised. */
export function surfaceColour(surface: string): string {
  return SURFACE_COLOURS[surface] ?? UNKNOWN_SURFACE_COLOUR;
}
