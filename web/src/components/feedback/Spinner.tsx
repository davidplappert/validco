/** A small inline busy indicator, hidden from screen readers. */
export default function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        aria-hidden="true"
        className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
      />
      {label}
    </span>
  );
}
