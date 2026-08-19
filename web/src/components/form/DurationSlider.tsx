import Field from "./Field";

/**
 * The time budget.
 *
 * A slider rather than a number field because the question is "roughly how long
 * have you got", and five-minute granularity is finer than anyone's answer. The
 * chosen value is in the label so it is announced when the slider moves.
 */
export default function DurationSlider({
  minutes,
  onChange,
  min = 10,
  max = 120,
  step = 5,
}: {
  minutes: number;
  onChange: (minutes: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <Field label={`How long do you have? — ${minutes} min`}>
      <input
        type="range"
        aria-label="Walk duration in minutes"
        min={min}
        max={max}
        step={step}
        value={minutes}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full"
      />
    </Field>
  );
}
