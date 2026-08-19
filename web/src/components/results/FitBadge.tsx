/**
 * The suitability score as a plain-language band.
 *
 * A word beats a number here: "Challenging" is immediately actionable, whereas
 * "47" invites the reader to guess at a scale nobody explained. The numeric
 * score is still shown, but next to its reasons.
 */

export interface FitTone {
  label: string;
  className: string;
}

/** Map a 0-100 score to a label and colour. */
export function fitTone(score: number): FitTone {
  if (score >= 85) return { label: "Great fit", className: "bg-emerald-500/15 text-emerald-300" };
  if (score >= 65) return { label: "Good fit", className: "bg-sky-500/15 text-sky-300" };
  if (score >= 45) return { label: "Challenging", className: "bg-amber-500/15 text-amber-300" };
  return { label: "Hard for you", className: "bg-rose-500/15 text-rose-300" };
}

export default function FitBadge({ score }: { score: number }) {
  const tone = fitTone(score);
  return (
    <span className={`shrink-0 rounded-full px-2 py-1 text-[11px] font-medium ${tone.className}`}>
      {tone.label}
    </span>
  );
}
