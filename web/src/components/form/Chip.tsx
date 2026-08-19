/** A toggleable pill. Uses `aria-pressed` so its state is announced. */
export default function Chip({
  label,
  active,
  title,
  onToggle,
}: {
  label: string;
  active: boolean;
  title?: string;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-pressed={active}
      onClick={onToggle}
      className={`rounded-full border px-2.5 py-1 text-[11px] transition ${
        active
          ? "border-accent/60 bg-accent/15 text-accent"
          : "border-line bg-surface text-ink-dim hover:border-accent/30"
      }`}
    >
      {label}
    </button>
  );
}
