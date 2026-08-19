"use client";

import { useState } from "react";
import type { WeightProjection } from "@/lib/types";

/**
 * What repeating this walk would do to body weight.
 *
 * The most requested number in any fitness app and the easiest to overstate,
 * so the presentation does three things deliberately:
 *
 * - It shows the **one-year** figure as the headline, not the first month. A
 *   month looks impressive and is the least representative horizon.
 * - It shows where the projection **settles**, because weight loss plateaus
 *   and pretending otherwise is the standard dishonesty here.
 * - It keeps the assumption visible: these numbers hold only if eating does
 *   not change, and people eat back much of what exercise burns.
 */
export default function WeightProjectionCard({
  projection,
}: {
  projection: WeightProjection;
}) {
  const options = projection.projections;
  const [selected, setSelected] = useState(() => {
    // Default to three sessions a week — the usual starting prescription, and
    // the one most likely to be sustained.
    const preferred = options.findIndex((option) => option.sessions_per_week === 3);
    return preferred >= 0 ? preferred : 0;
  });

  const current = options[selected];
  if (!current) return null;

  return (
    <div className="rounded-lg bg-ground/60 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="text-[11px] uppercase tracking-wide text-ink-dim">
          If you walked this regularly
        </div>
        <div className="flex gap-1" role="group" aria-label="Walks per week">
          {options.map((option, index) => (
            <button
              key={option.sessions_per_week}
              type="button"
              aria-pressed={index === selected}
              onClick={(event) => {
                // The card lives inside a route card that is itself a button;
                // without this the click would also collapse the route.
                event.stopPropagation();
                setSelected(index);
              }}
              className={`rounded px-1.5 py-0.5 text-[11px] tabular-nums transition ${
                index === selected
                  ? "bg-accent/20 text-accent"
                  : "text-ink-dim hover:text-ink"
              }`}
            >
              {option.sessions_per_week}×
            </button>
          ))}
        </div>
      </div>

      {current.meaningful ? (
        <>
          <div className="mb-2 grid grid-cols-3 gap-2">
            <Figure label="First month" value={current.first_month_lb} />
            <Figure label="One year" value={current.one_year_lb} emphasis />
            <Figure label="Settles at" value={current.eventual_lb} />
          </div>
          <div className="text-xs text-ink-dim">
            {current.weekly_kcal.toLocaleString("en-US")} kcal a week above resting —
            about {current.eventual_pct_of_body_weight}% of your body weight, eventually.
          </div>
        </>
      ) : (
        <div className="text-xs text-ink-dim">
          At {current.sessions_per_week}× a week this walk is too short to move the
          scale on its own — though it still counts toward your steps and heart health.
        </div>
      )}

      <p className="mt-2 text-[10px] leading-relaxed text-ink-dim">{projection.note}</p>
    </div>
  );
}

/** One projected figure, in pounds. */
function Figure({
  label,
  value,
  emphasis = false,
}: {
  label: string;
  value: number;
  emphasis?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[10px] uppercase tracking-wide text-ink-dim">
        {label}
      </div>
      <div
        className={`truncate tabular-nums ${
          emphasis ? "text-base font-semibold text-accent" : "text-sm font-medium"
        }`}
      >
        −{value.toFixed(1)} lb
      </div>
    </div>
  );
}
