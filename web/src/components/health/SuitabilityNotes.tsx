import type { Suitability } from "@/lib/types";

/**
 * Why a route does or does not suit this walker.
 *
 * The score is only ever shown next to its reasons. A bare number invites more
 * trust than the model has earned, and "includes a 22% grade" is the genuinely
 * useful output.
 */
export default function SuitabilityNotes({ suitability }: { suitability: Suitability }) {
  return (
    <div className="rounded-lg bg-ground/60 p-3 text-xs text-ink-dim">
      <div className="mb-1 font-medium text-ink">Why this fits (score {suitability.score}/100)</div>
      <ul className="list-inside list-disc space-y-1">
        {suitability.notes.map((note, index) => (
          <li key={index}>{note}</li>
        ))}
      </ul>
    </div>
  );
}
