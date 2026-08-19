import { controlClass } from "./Field";

/** A single-line text input using the shared control styling. */
export default function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${controlClass} ${props.className ?? ""}`} />;
}
