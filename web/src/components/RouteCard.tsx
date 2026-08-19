"use client";

import type { Route } from "@/lib/api";
import ElevationProfile from "./ElevationProfile";
import { SURFACE_COLOURS } from "./MapView";

/**
 * One suggested walk.
 *
 * The information hierarchy is the argument the product is making: time and
 * distance first (what you asked for), then the health return (why you'd go),
 * then the surface mix and terrain (what it will actually be like), then the
 * caveats. The "fit" score is shown next to its reasons rather than alone,
 * because a bare number invites more trust than the model has earned.
 */

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[11px] uppercase tracking-wide text-ink-dim">{label}</div>
      <div className="truncate text-lg font-semibold tabular-nums">{value}</div>
      {sub && <div className="truncate text-[11px] text-ink-dim">{sub}</div>}
    </div>
  );
}

function fitTone(score: number) {
  if (score >= 85) return { label: "Great fit", cls: "bg-emerald-500/15 text-emerald-300" };
  if (score >= 65) return { label: "Good fit", cls: "bg-sky-500/15 text-sky-300" };
  if (score >= 45) return { label: "Challenging", cls: "bg-amber-500/15 text-amber-300" };
  return { label: "Hard for you", cls: "bg-rose-500/15 text-rose-300" };
}

export default function RouteCard({
  route,
  selected,
  onSelect,
}: {
  route: Route;
  selected: boolean;
  onSelect: () => void;
}) {
  const e = route.effort;
  const h = route.health;
  const fit = fitTone(route.suitability.score);
  const surfaces = Object.entries(route.surface_breakdown_pct).sort((a, b) => b[1] - a[1]);

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`animate-fade-up w-full rounded-xl border p-4 text-left transition ${
        selected
          ? "border-accent/70 bg-surface-2 shadow-lg shadow-black/30"
          : "border-line bg-surface hover:border-accent/40 hover:bg-surface-2"
      }`}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-medium">
            {route.destination ? `Out to ${route.destination.name}` : "Neighbourhood loop"}
          </div>
          <div className="truncate text-xs text-ink-dim">
            {route.shape === "loop" ? "Loop" : "Out and back"}
            {route.streets.length > 0 && ` · via ${route.streets.slice(0, 3).join(", ")}`}
          </div>
        </div>
        <span className={`shrink-0 rounded-full px-2 py-1 text-[11px] font-medium ${fit.cls}`}>
          {fit.label}
        </span>
      </div>

      <div className="mb-3 grid grid-cols-4 gap-3">
        <Stat label="Time" value={`${Math.round(e.duration_min)} min`} sub={`${e.avg_speed_mph} mph`} />
        <Stat label="Distance" value={`${e.distance_mi} mi`} sub={`${e.steps.toLocaleString()} steps`} />
        <Stat label="Climb" value={`${e.ascent_ft} ft`} sub={`peak ${e.peak_grade_pct}%`} />
        <Stat label="Energy" value={`${e.kcal_gross} kcal`} sub={`${e.mets} MET · ${e.intensity}`} />
      </div>

      {/* Surface mix — the "road or walking path" question, answered visually
          and with the same colours the map uses. */}
      <div className="mb-3">
        <div className="mb-1 flex h-2 w-full overflow-hidden rounded-full bg-ground">
          {surfaces.map(([surface, pct]) => (
            <div
              key={surface}
              style={{ width: `${pct}%`, backgroundColor: SURFACE_COLOURS[surface] ?? "#94a3b8" }}
              title={`${surface}: ${pct}%`}
            />
          ))}
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-dim">
          {surfaces.map(([surface, pct]) => (
            <span key={surface} className="inline-flex items-center gap-1">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: SURFACE_COLOURS[surface] ?? "#94a3b8" }}
              />
              {route.surface_labels[surface] ?? surface} {pct}%
            </span>
          ))}
        </div>
      </div>

      {selected && (
        <div className="mt-4 space-y-4 border-t border-line pt-4">
          <ElevationProfile route={route} />

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg bg-ground/60 p-3">
              <div className="mb-1 text-[11px] uppercase tracking-wide text-ink-dim">
                Weekly activity target
              </div>
              <div className="mb-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
                <div
                  className="h-full bg-accent"
                  style={{
                    width: `${Math.min(100, h.guideline_progress.pct_of_weekly_target)}%`,
                  }}
                />
              </div>
              <div className="text-xs text-ink-dim">
                {h.guideline_progress.pct_of_weekly_target}% of the WHO 150 min/week of
                moderate activity
                {!h.guideline_progress.counts_as_moderate && " (this pace counts as light)"}
              </div>
            </div>

            <div className="rounded-lg bg-ground/60 p-3">
              <div className="mb-1 text-[11px] uppercase tracking-wide text-ink-dim">
                Daily steps
              </div>
              <div className="mb-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
                <div
                  className="h-full bg-accent"
                  style={{ width: `${Math.min(100, h.steps.pct_of_daily_target)}%` }}
                />
              </div>
              <div className="text-xs text-ink-dim">
                {h.steps.walk_steps.toLocaleString()} steps —{" "}
                {h.steps.pct_of_daily_target}% of a 7,000-step day
              </div>
            </div>
          </div>

          <div className="rounded-lg bg-ground/60 p-3 text-xs text-ink-dim">
            <div className="mb-1 font-medium text-ink">Why this fits (score {route.suitability.score}/100)</div>
            <ul className="list-inside list-disc space-y-1">
              {route.suitability.notes.map((note, i) => (
                <li key={i}>{note}</li>
              ))}
            </ul>
          </div>

          <div className="grid gap-3 text-xs text-ink-dim sm:grid-cols-2">
            <div className="rounded-lg bg-ground/60 p-3">
              <div className="mb-1 font-medium text-ink">Energy</div>
              <div>
                {h.energy.kcal_gross} kcal total, {h.energy.kcal_net_of_resting} kcal above resting.
              </div>
              <div className="mt-1">{h.energy.note}</div>
            </div>
            <div className="rounded-lg bg-ground/60 p-3">
              <div className="mb-1 font-medium text-ink">Joint loading</div>
              <div>
                Peak knee force ≈ {h.joint_load.peak_knee_force_bw}× body weight (
                {h.joint_load.peak_knee_force_lb.toLocaleString()} lb) per step.
              </div>
            </div>
          </div>

          {route.features.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {route.features.map((f) => (
                <span
                  key={f}
                  className="rounded-full border border-line bg-ground/60 px-2 py-0.5 text-[11px] text-ink-dim"
                >
                  {f}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </button>
  );
}
