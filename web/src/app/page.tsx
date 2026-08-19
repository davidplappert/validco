"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import {
  ApiError,
  getRegions,
  plan,
  type PlanResponse,
  type Preferences,
  type Region,
  type Sex,
} from "@/lib/api";
import RouteCard from "@/components/RouteCard";

// MapLibre touches `window` on import, so it must stay out of the static
// prerender that `next build` runs at export time.
const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => <div className="absolute inset-0 bg-ground" />,
});

const DEFAULT_PREFS: Preferences = {
  prefer_paths: true,
  avoid_hills: false,
  avoid_stairs: false,
  avoid_busy_roads: true,
  prefer_green: false,
};

const PREF_LABELS: { key: keyof Preferences; label: string; hint: string }[] = [
  { key: "prefer_paths", label: "Prefer paths", hint: "Favour footpaths and park trails over roadway" },
  { key: "avoid_hills", label: "Avoid hills", hint: "Take flatter routes even if they're longer" },
  { key: "avoid_stairs", label: "No stairs", hint: "Exclude stepped sections entirely" },
  { key: "avoid_busy_roads", label: "Avoid busy roads", hint: "Steer away from arterial traffic" },
  { key: "prefer_green", label: "Prefer green", hint: "Favour routes near parks and gardens" },
];

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] uppercase tracking-wide text-ink-dim">{label}</span>
      {children}
    </label>
  );
}

const inputCls =
  "w-full rounded-lg border border-line bg-ground px-3 py-2 text-sm outline-none " +
  "placeholder:text-ink-dim/60 focus:border-accent focus:ring-1 focus:ring-accent";

export default function Home() {
  const [address, setAddress] = useState("");
  const [sex, setSex] = useState<Sex>("male");
  const [age, setAge] = useState("33");
  const [weightLb, setWeightLb] = useState("180");
  const [heightFt, setHeightFt] = useState("5");
  const [heightIn, setHeightIn] = useState("10");
  const [minutes, setMinutes] = useState(30);
  const [prefs, setPrefs] = useState<Preferences>(DEFAULT_PREFS);

  const [regions, setRegions] = useState<Region[]>([]);
  const [result, setResult] = useState<PlanResponse | null>(null);
  const [selected, setSelected] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; hint?: string } | null>(null);

  useEffect(() => {
    getRegions()
      .then((r) => setRegions(r.regions))
      .catch((err) => console.warn("[stepwise] could not load regions", err));
  }, []);

  const center = useMemo<[number, number]>(() => {
    if (result) return [result.origin.lat, result.origin.lon];
    return regions[0] ? regions[0].center : [37.7749, -122.4194];
  }, [result, regions]);

  const onSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setBusy(true);
      setError(null);
      try {
        const response = await plan({
          address: address.trim(),
          minutes,
          profile: {
            sex,
            age: Number(age),
            weight_lb: Number(weightLb),
            height_ft: heightFt ? Number(heightFt) : undefined,
            height_in: heightIn ? Number(heightIn) : undefined,
          },
          preferences: prefs,
          max_routes: 4,
        });
        setResult(response);
        setSelected(0);
      } catch (err) {
        // Surface the API's own guidance — it returns street suggestions on a
        // geocoding miss, which is far more useful than "not found".
        if (err instanceof ApiError) {
          const sugg = err.detail.suggestions as Record<string, string[] | string> | undefined;
          const flat = sugg
            ? Object.values(sugg)
                .flatMap((v) => (Array.isArray(v) ? v : []))
                .slice(0, 4)
            : [];
          setError({
            message: err.message,
            hint: flat.length ? `Did you mean: ${flat.join(", ")}?` : undefined,
          });
        } else {
          setError({ message: err instanceof Error ? err.message : "Something went wrong" });
        }
        setResult(null);
      } finally {
        setBusy(false);
      }
    },
    [address, minutes, sex, age, weightLb, heightFt, heightIn, prefs],
  );

  const activeRoute = result?.routes[selected] ?? null;

  return (
    <main className="relative h-dvh w-full overflow-hidden">
      <MapView
        route={activeRoute}
        origin={result ? { lat: result.origin.snapped_lat, lon: result.origin.snapped_lon } : null}
        center={center}
      />

      <div className="pointer-events-none absolute inset-0 flex justify-end p-3 sm:p-4">
        <div className="pointer-events-auto flex w-full max-w-[430px] flex-col gap-3 overflow-y-auto rounded-2xl border border-line bg-ground/92 p-4 backdrop-blur-md">
          <header className="flex items-baseline justify-between gap-2">
            <div>
              <h1 className="text-lg font-semibold tracking-tight">
                StepWise<span className="text-accent">.</span>
              </h1>
              <p className="text-[11px] text-ink-dim">
                Walking routes scored for your body, on Overture Maps data
              </p>
            </div>
            <span className="rounded-full border border-accent/40 px-2 py-0.5 text-[10px] uppercase tracking-wider text-accent">
              dev
            </span>
          </header>

          <form onSubmit={onSubmit} className="flex flex-col gap-3">
            <Field label="Start address">
              <input
                className={inputCls}
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="100 N Main St, Chillicothe, IL"
                required
                autoComplete="street-address"
              />
            </Field>

            <div className="grid grid-cols-3 gap-2">
              <Field label="Sex">
                <select
                  className={inputCls}
                  value={sex}
                  onChange={(e) => setSex(e.target.value as Sex)}
                >
                  <option value="male">Male</option>
                  <option value="female">Female</option>
                </select>
              </Field>
              <Field label="Age">
                <input
                  className={inputCls}
                  type="number"
                  min={13}
                  max={110}
                  value={age}
                  onChange={(e) => setAge(e.target.value)}
                  required
                />
              </Field>
              <Field label="Weight (lb)">
                <input
                  className={inputCls}
                  type="number"
                  min={55}
                  max={880}
                  value={weightLb}
                  onChange={(e) => setWeightLb(e.target.value)}
                  required
                />
              </Field>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <Field label="Height (ft)">
                <input
                  className={inputCls}
                  type="number"
                  min={3}
                  max={8}
                  value={heightFt}
                  onChange={(e) => setHeightFt(e.target.value)}
                />
              </Field>
              <Field label="Height (in)">
                <input
                  className={inputCls}
                  type="number"
                  min={0}
                  max={11}
                  value={heightIn}
                  onChange={(e) => setHeightIn(e.target.value)}
                />
              </Field>
            </div>

            <Field label={`How long do you have? — ${minutes} min`}>
              <input
                type="range"
                min={10}
                max={120}
                step={5}
                value={minutes}
                onChange={(e) => setMinutes(Number(e.target.value))}
                className="w-full"
              />
            </Field>

            <div className="flex flex-wrap gap-1.5">
              {PREF_LABELS.map(({ key, label, hint }) => (
                <button
                  key={key}
                  type="button"
                  title={hint}
                  onClick={() => setPrefs((p) => ({ ...p, [key]: !p[key] }))}
                  className={`rounded-full border px-2.5 py-1 text-[11px] transition ${
                    prefs[key]
                      ? "border-accent/60 bg-accent/15 text-accent"
                      : "border-line bg-surface text-ink-dim hover:border-accent/30"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-ground transition hover:bg-accent-dim hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? "Finding walks…" : "Find me a walk"}
            </button>
          </form>

          {error && (
            <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200">
              <div className="font-medium">{error.message}</div>
              {error.hint && <div className="mt-1 text-rose-300/80">{error.hint}</div>}
              {regions.length > 0 && (
                <div className="mt-2 text-rose-300/70">
                  Covered areas: {regions.map((r) => r.label).join(" · ")}
                </div>
              )}
            </div>
          )}

          {result && (
            <>
              <div className="rounded-lg border border-line bg-surface p-3 text-xs text-ink-dim">
                <div className="text-ink">{result.origin.label ?? "Your start"}</div>
                <div className="mt-1">
                  BMI {result.profile.bmi} ({result.profile.bmi_class}) · comfortable pace{" "}
                  {result.profile.baseline_speed_mph} mph · resting {result.profile.rmr_kcal_day} kcal/day
                </div>
                <div className="mt-1">
                  Snapped {result.origin.snap_distance_m} m to the walking network ·{" "}
                  {result.routes.length} routes in {Math.round(result.timing_ms.plan)} ms
                </div>
              </div>

              <div className="flex flex-col gap-2">
                {result.routes.map((route, i) => (
                  <RouteCard
                    key={route.id}
                    route={route}
                    selected={i === selected}
                    onSelect={() => setSelected(i)}
                  />
                ))}
              </div>

              <p className="text-[10px] leading-relaxed text-ink-dim">
                {result.routes[selected]?.health.caveats.join(" ")}
              </p>
            </>
          )}

          <footer className="mt-auto pt-2 text-[10px] leading-relaxed text-ink-dim">
            Places, roads and addresses © Overture Maps Foundation and OpenStreetMap
            contributors. Elevation from USGS 3DEP via AWS Terrain Tiles. Estimates only —
            not medical advice.
          </footer>
        </div>
      </div>
    </main>
  );
}
