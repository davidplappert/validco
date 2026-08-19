import { controlClass } from "./Field";

export interface Option<T extends string> {
  value: T;
  label: string;
}

/** A select bound to a string-union type. */
export default function SelectInput<T extends string>({
  value,
  options,
  onValueChange,
  ...rest
}: {
  value: T;
  options: Option<T>[];
  onValueChange: (value: T) => void;
} & Omit<React.SelectHTMLAttributes<HTMLSelectElement>, "value" | "onChange">) {
  return (
    <select
      {...rest}
      value={value}
      onChange={(event) => onValueChange(event.target.value as T)}
      className={`${controlClass} ${rest.className ?? ""}`}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
