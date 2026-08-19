/**
 * Display formatting.
 *
 * Centralised so a number is rendered the same way everywhere, and so the
 * rounding rules are testable on their own rather than scattered through JSX.
 */

/** Thousands separators, e.g. `3,599`. */
export function thousands(value: number): string {
  return Math.round(value).toLocaleString("en-US");
}

/** Whole minutes with a unit, e.g. `34 min`. */
export function minutes(value: number): string {
  return `${Math.round(value)} min`;
}

/** Miles to two decimals, e.g. `1.32 mi`. */
export function miles(value: number): string {
  return `${value.toFixed(2)} mi`;
}

/** Whole feet with a unit, e.g. `232 ft`. */
export function feet(value: number): string {
  return `${Math.round(value)} ft`;
}

/** Whole kilocalories with a unit. */
export function kcal(value: number): string {
  return `${Math.round(value)} kcal`;
}

/**
 * A percentage clamped to 0-100, for progress bar widths.
 *
 * NaN becomes 0 because there is nothing to show; infinity becomes 100 because
 * it means "past the target", not "unknown". Collapsing both to 0 would render
 * an over-target bar as empty.
 */
export function clampPercent(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

/**
 * Pace as `mm:ss /mi`.
 *
 * Pace is conventionally read as minutes and seconds, not as a decimal — `21:36`
 * rather than `21.6`.
 */
export function pace(minutesPerMile: number): string {
  if (!Number.isFinite(minutesPerMile) || minutesPerMile <= 0) return "—";
  const whole = Math.floor(minutesPerMile);
  const seconds = Math.round((minutesPerMile - whole) * 60);
  // Rounding up to 60 seconds must carry into the minutes.
  const carried = seconds === 60 ? whole + 1 : whole;
  const shown = seconds === 60 ? 0 : seconds;
  return `${carried}:${String(shown).padStart(2, "0")} /mi`;
}

/** Title-case a surface or category key, e.g. `dog_park` to `Dog park`. */
export function humanise(key: string): string {
  const spaced = key.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
