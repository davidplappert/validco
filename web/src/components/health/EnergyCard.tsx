import type { EnergyReport } from "@/lib/types";

/**
 * Calories, with the gross/net split shown rather than hidden.
 *
 * Most apps report one number without saying which; at a high body mass the
 * resting share of a 30-minute walk is a fifth of the total.
 */
export default function EnergyCard({ energy }: { energy: EnergyReport }) {
  return (
    <div className="rounded-lg bg-ground/60 p-3 text-xs text-ink-dim">
      <div className="mb-1 font-medium text-ink">Energy</div>
      <div>
        {energy.kcal_gross} kcal total, {energy.kcal_net_of_resting} kcal above resting.
      </div>
      <div className="mt-1">{energy.note}</div>
    </div>
  );
}
