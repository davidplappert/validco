"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { PlanRequest } from "@/lib/api";
import type { Preferences, Sex } from "@/lib/types";
import type { AddressSuggestion } from "@/lib/suggest";
import { ADJUSTING_DELAY_MS, TYPING_DELAY_MS, useSettledValue } from "@/hooks/useDebouncedValue";
import AddressAutocomplete from "./AddressAutocomplete";
import DurationSlider from "./DurationSlider";
import Field from "./Field";
import NumberInput from "./NumberInput";
import PreferenceChips from "./PreferenceChips";
import SelectInput from "./SelectInput";

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
 * The request the app runs on load, so the first screen shows a real route.
 *
 * Mirrors the form's own initial state, and is exported from here rather than
 * duplicated in the page so the two can never drift — what loads is exactly
 * what the form says it will submit.
 */
export const DEFAULT_PLAN_REQUEST = {
  address: DEFAULT_ADDRESS,
  minutes: 30,
  profile: {
    sex: "male" as const,
    age: 33,
    weight_lb: 180,
    height_ft: 5,
    height_in: 10,
  },
  preferences: DEFAULT_PREFERENCES,
  max_routes: 4,
};

/**
 * Everything the form knows, in one value.
 *
 * Debouncing per field would fire three overlapping plans for someone who
 * changes their weight and then their age. Debouncing the whole snapshot
 * coalesces those into one request while still letting the *delay* depend on
 * which field moved — see `delayFor` below.
 */
interface FormState {
  address: string;
  /** Coordinates from a chosen suggestion, if the address came from the list. */
  origin: { lat: number; lon: number; region?: string } | null;
  sex: Sex;
  age: string;
  weightLb: string;
  heightFt: string;
  heightIn: string;
  minutes: number;
  preferences: Preferences;
}

/** Field-wise equality: the snapshot is a fresh object on every render. */
function sameForm(a: FormState, b: FormState): boolean {
  return (
    a.address === b.address &&
    a.origin?.lat === b.origin?.lat &&
    a.origin?.lon === b.origin?.lon &&
    a.sex === b.sex &&
    a.age === b.age &&
    a.weightLb === b.weightLb &&
    a.heightFt === b.heightFt &&
    a.heightIn === b.heightIn &&
    a.minutes === b.minutes &&
    a.preferences === b.preferences
  );
}

/**
 * How long to wait, given what just changed.
 *
 * An address typed by hand gets the long wait: its intermediate values are not
 * addresses at all. An address *chosen from the dropdown* is already complete,
 * so it takes the short one — waiting after a deliberate click would just feel
 * broken. Everything else is a number the user is converging on and watching.
 */
function delayFor(next: FormState, previous: FormState): number {
  const addressTypedByHand = next.address !== previous.address && !next.origin;
  return addressTypedByHand ? TYPING_DELAY_MS : ADJUSTING_DELAY_MS;
}

/**
 * Whether a snapshot is worth sending.
 *
 * Nothing runs the browser's validation pass on the automatic path, so the
 * bounds declared on the inputs have to be enforced here as well. Without this, a
 * cleared age field sends `age: NaN`, which the API rejects — turning an
 * ordinary edit into an error message.
 */
function isComplete(form: FormState): boolean {
  const age = Number(form.age);
  const weight = Number(form.weightLb);
  const hasPlace = Boolean(form.origin) || form.address.trim().length > 3;
  return (
    hasPlace &&
    Number.isFinite(age) &&
    age >= 13 &&
    age <= 110 &&
    Number.isFinite(weight) &&
    weight >= 55 &&
    weight <= 880
  );
}

/** Turn a settled snapshot into the request the API expects. */
function toRequest(form: FormState): PlanRequest {
  return {
    // Coordinates win when the address came from the dropdown: the suggestion
    // already resolved to a point, so sending it back as text would ask the
    // server to geocode a string it just produced.
    ...(form.origin
      ? {
          lat: form.origin.lat,
          lon: form.origin.lon,
          region: form.origin.region,
        }
      : { address: form.address.trim() }),
    minutes: form.minutes,
    profile: {
      sex: form.sex,
      age: Number(form.age),
      weight_lb: Number(form.weightLb),
      // Blank height fields are omitted rather than sent as zero, so the API
      // can apply its documented population default.
      height_ft: form.heightFt ? Number(form.heightFt) : undefined,
      height_in: form.heightIn ? Number(form.heightIn) : undefined,
    },
    preferences: form.preferences,
    max_routes: 4,
  };
}

/**
 * Everything the planner needs from the user.
 *
 * **Nobody has to press anything.** The form plans itself as you type, once the
 * value has settled — which is the whole reason `useSettledValue` takes a
 * per-change delay rather than one constant. A submit button still exists in
 * the document, visually hidden until focused, because HTML needs one for
 * Enter to submit and because a form that acts on a timer otherwise gives an
 * assistive-technology user no way to say "now". Height is optional, and the
 * API falls back to population means and says so.
 *
 * Every field arrives with a value, the address included, so a first-time
 * visitor sees a real answer before deciding whether to type their own details
 * in.
 */
export default function PlanForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (request: PlanRequest) => void;
}) {
  const [address, setAddress] = useState(DEFAULT_ADDRESS);
  const [origin, setOrigin] = useState<FormState["origin"]>(null);
  const [sex, setSex] = useState<Sex>("male");
  const [age, setAge] = useState("33");
  const [weightLb, setWeightLb] = useState("180");
  const [heightFt, setHeightFt] = useState("5");
  const [heightIn, setHeightIn] = useState("10");
  const [minutes, setMinutes] = useState(30);
  const [preferences, setPreferences] = useState<Preferences>(DEFAULT_PREFERENCES);

  const form = useMemo<FormState>(
    () => ({
      address,
      origin,
      sex,
      age,
      weightLb,
      heightFt,
      heightIn,
      minutes,
      preferences,
    }),
    [address, origin, sex, age, weightLb, heightFt, heightIn, minutes, preferences],
  );

  const { value: settled } = useSettledValue(form, {
    delayFor,
    isEqual: sameForm,
  });

  // The page already plans DEFAULT_PLAN_REQUEST on mount, and this hook's first
  // emission is that same snapshot — so without this guard the app would open
  // by running two identical plans.
  const submittedOnce = useRef(false);
  useEffect(() => {
    if (!submittedOnce.current) {
      submittedOnce.current = true;
      return;
    }
    if (!isComplete(settled)) return;
    onSubmit(toRequest(settled));
  }, [settled, onSubmit]);

  /**
   * Enter still submits, and skips the wait.
   *
   * Someone who finishes typing and presses Enter has said they are done;
   * making them sit through the debounce would read as the app ignoring them.
   */
  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    submittedOnce.current = true;
    if (isComplete(form)) onSubmit(toRequest(form));
  };

  /**
   * A chosen suggestion carries its own coordinates, so the geocode is already
   * done. Typing again drops them — the text no longer describes that point.
   */
  const handleSelect = (suggestion: AddressSuggestion) => {
    setAddress(suggestion.value);
    setOrigin(
      suggestion.lat !== null && suggestion.lon !== null
        ? {
            lat: suggestion.lat,
            lon: suggestion.lon,
            region: suggestion.region,
          }
        : null,
    );
  };

  const handleAddressChange = (next: string) => {
    setAddress(next);
    setOrigin(null);
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <Field label="Start address">
        <AddressAutocomplete
          value={address}
          onValueChange={handleAddressChange}
          onSelect={handleSelect}
          placeholder="100 N Main St, Morton, IL"
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

      {/*
        The submit button is gone from view, not from the document, and that is
        deliberate on two counts.

        HTML only performs implicit submission — Enter in a text field — when a
        form has a submit button, or exactly one field. This form has nine, so
        deleting the button outright silently broke Enter in every browser, not
        just in the test that caught it.

        It is also the accessible escape hatch. A form that submits itself on a
        timer gives a screen-reader or switch user no way to say "now"; this
        gives them one, and `focus:not-sr-only` means a sighted keyboard user
        who tabs onto it can see what they have landed on rather than
        interacting with something invisible.
      */}
      <button
        type="submit"
        disabled={busy}
        className="sr-only rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-ground focus:not-sr-only focus:outline-2 focus:outline-offset-2 focus:outline-accent"
      >
        Update walks now
      </button>

      {/*
        A form that acts on its own still owes the user a statement of what it
        is doing — silence while the map redraws itself reads as a glitch.
      */}
      <p aria-live="polite" className="text-center text-xs text-ink-dim">
        {busy ? "Updating your walks…" : "Walks update as you type."}
      </p>
    </form>
  );
}
