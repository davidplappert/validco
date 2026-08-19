"use client";

import { useCallback, useState } from "react";
import type { PlanRequest } from "@/lib/api";
import type { Preferences, Sex } from "@/lib/types";
import Spinner from "@/components/feedback/Spinner";
import DurationSlider from "./DurationSlider";
import Field from "./Field";
import NumberInput from "./NumberInput";
import PreferenceChips from "./PreferenceChips";
import SelectInput from "./SelectInput";
import TextInput from "./TextInput";

/**
 * Where the form starts: Chillicothe City Hall, in the covered Peoria region.
 *
 * A civic building, not a residence — the address is published by the city, so
 * it names a public office rather than anybody's home, and the app is usable
 * the moment it loads instead of demanding an address before it will do
 * anything. Overture spells the street "North Second Street" and the geocoder's
 * normalizer does not fold "2nd" into "Second", so the ordinal is written out;
 * abbreviated, the lookup misses.
 */
export const DEFAULT_ADDRESS = "908 N Second St, Chillicothe, IL 61523";

/** Starting preferences: most people want a footpath and no traffic. */
export const DEFAULT_PREFERENCES: Preferences = {
  prefer_paths: true,
  avoid_hills: false,
  avoid_stairs: false,
  avoid_busy_roads: true,
  prefer_green: false,
};

const SEX_OPTIONS: { value: Sex; label: string }[] = [
  { value: "male", label: "Male" },
  { value: "female", label: "Female" },
];

/**
 * Everything the planner needs from the user.
 *
 * Owns its own field state and emits a fully-formed request on submit, so the
 * page never touches individual inputs. Height is optional — the API falls back
 * to population means and says so — but it is asked for because it materially
 * improves resting metabolism and step count.
 *
 * Every field arrives with a value, the address included, so a first-time
 * visitor can press the button and see a real answer before deciding whether to
 * type their own details in. The placeholder still shows the expected format,
 * for whoever clears the field.
 */
/**
 * The request the app runs on load, so the first screen shows a real route.
 *
 * Mirrors the form's own initial state, and is exported from here rather than
 * duplicated in the page so the two can never drift — what loads is exactly
 * what the form says it will submit.
 */
export const DEFAULT_PLAN_REQUEST = {
  address: DEFAULT_ADDRESS,
  minutes: 30,
  profile: { sex: "male" as const, age: 33, weight_lb: 180, height_ft: 5, height_in: 10 },
  preferences: DEFAULT_PREFERENCES,
  max_routes: 4,
};

export default function PlanForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (request: PlanRequest) => void;
}) {
  const [address, setAddress] = useState(DEFAULT_ADDRESS);
  const [sex, setSex] = useState<Sex>("male");
  const [age, setAge] = useState("33");
  const [weightLb, setWeightLb] = useState("180");
  const [heightFt, setHeightFt] = useState("5");
  const [heightIn, setHeightIn] = useState("10");
  const [minutes, setMinutes] = useState(30);
  const [preferences, setPreferences] = useState<Preferences>(DEFAULT_PREFERENCES);

  const handleSubmit = useCallback(
    (event: React.FormEvent) => {
      event.preventDefault();
      onSubmit({
        address: address.trim(),
        minutes,
        profile: {
          sex,
          age: Number(age),
          weight_lb: Number(weightLb),
          // Blank height fields are omitted rather than sent as zero, so the
          // API can apply its documented population default.
          height_ft: heightFt ? Number(heightFt) : undefined,
          height_in: heightIn ? Number(heightIn) : undefined,
        },
        preferences,
        max_routes: 4,
      });
    },
    [address, minutes, sex, age, weightLb, heightFt, heightIn, preferences, onSubmit],
  );

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <Field label="Start address">
        <TextInput
          value={address}
          onChange={(event) => setAddress(event.target.value)}
          placeholder="100 N Main St, Morton, IL"
          autoComplete="street-address"
          required
        />
      </Field>

      <div className="grid grid-cols-3 gap-2">
        <Field label="Sex">
          <SelectInput value={sex} options={SEX_OPTIONS} onValueChange={setSex} />
        </Field>
        <Field label="Age">
          <NumberInput value={age} onValueChange={setAge} min={13} max={110} required />
        </Field>
        <Field label="Weight (lb)">
          <NumberInput value={weightLb} onValueChange={setWeightLb} min={55} max={880} required />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Field label="Height (ft)">
          <NumberInput value={heightFt} onValueChange={setHeightFt} min={3} max={8} />
        </Field>
        <Field label="Height (in)">
          <NumberInput value={heightIn} onValueChange={setHeightIn} min={0} max={11} />
        </Field>
      </div>

      <DurationSlider minutes={minutes} onChange={setMinutes} />
      <PreferenceChips preferences={preferences} onChange={setPreferences} />

      <button
        type="submit"
        disabled={busy}
        className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-ground transition hover:bg-accent-dim hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? <Spinner label="Finding walks…" /> : "Find me a walk"}
      </button>
    </form>
  );
}
