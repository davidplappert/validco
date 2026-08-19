/**
 * Data attribution and the medical disclaimer.
 *
 * Overture and OpenStreetMap both require attribution, and the disclaimer is
 * not optional for anything presenting calorie and joint-loading figures.
 */
export default function Attribution() {
  return (
    <footer className="mt-auto pt-2 text-[10px] leading-relaxed text-ink-dim">
      Places, roads and addresses © Overture Maps Foundation and OpenStreetMap contributors.
      Elevation from USGS 3DEP via AWS Terrain Tiles. Estimates only — not medical advice.
    </footer>
  );
}
