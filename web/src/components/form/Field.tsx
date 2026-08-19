/**
 * A labelled form control.
 *
 * Uses a wrapping `<label>` so the caption is associated with whatever control
 * is passed as children, without needing an id on every input.
 */
export default function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] uppercase tracking-wide text-ink-dim">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[10px] text-ink-dim">{hint}</span>}
    </label>
  );
}

/** Shared control styling, exported so every input looks identical. */
export const controlClass =
  "w-full rounded-lg border border-line bg-ground px-3 py-2 text-sm outline-none " +
  "placeholder:text-ink-dim/60 focus:border-accent focus:ring-1 focus:ring-accent";
