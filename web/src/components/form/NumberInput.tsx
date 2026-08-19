import { controlClass } from "./Field";

/**
 * A numeric input.
 *
 * The value is held as a string rather than a number so the field can be
 * cleared while typing. Turning "" into 0 on every keystroke makes the input
 * fight the user.
 */
export default function NumberInput({
  value,
  onValueChange,
  ...rest
}: {
  value: string;
  onValueChange: (value: string) => void;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  return (
    <input
      {...rest}
      type="number"
      inputMode="numeric"
      value={value}
      onChange={(event) => onValueChange(event.target.value)}
      className={`${controlClass} ${rest.className ?? ""}`}
    />
  );
}
