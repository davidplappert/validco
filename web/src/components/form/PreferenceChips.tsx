import type { Preferences } from "@/lib/types";
import Chip from "./Chip";

/** The five preference toggles, with what each one actually changes. */
export const PREFERENCE_OPTIONS: {
  key: keyof Preferences;
  label: string;
  hint: string;
}[] = [
  {
    key: "prefer_paths",
    label: "Prefer paths",
    hint: "Favour footpaths and park trails over walking in the roadway",
  },
  { key: "avoid_hills", label: "Avoid hills", hint: "Take flatter routes even if they are longer" },
  { key: "avoid_stairs", label: "No stairs", hint: "Exclude stepped sections entirely" },
  {
    key: "avoid_busy_roads",
    label: "Avoid busy roads",
    hint: "Steer away from arterial traffic",
  },
  { key: "prefer_green", label: "Prefer green", hint: "Favour routes near parks and gardens" },
];

/** The preference chip row. */
export default function PreferenceChips({
  preferences,
  onChange,
}: {
  preferences: Preferences;
  onChange: (preferences: Preferences) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Route preferences">
      {PREFERENCE_OPTIONS.map(({ key, label, hint }) => (
        <Chip
          key={key}
          label={label}
          title={hint}
          active={preferences[key]}
          onToggle={() => onChange({ ...preferences, [key]: !preferences[key] })}
        />
      ))}
    </div>
  );
}
