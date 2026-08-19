/**
 * The limits of the estimates.
 *
 * Rendered from the API's own caveat list rather than hard-coded copy, so the
 * disclaimers cannot drift away from the model that produced the numbers — and
 * so a caveat added server-side (an assumed height, say) appears without a
 * frontend change.
 */
export default function Caveats({ caveats }: { caveats: string[] }) {
  if (caveats.length === 0) return null;
  return <p className="text-[10px] leading-relaxed text-ink-dim">{caveats.join(" ")}</p>;
}
