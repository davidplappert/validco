/** One headline figure with a caption and optional secondary line. */
export default function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="min-w-0">
      <div className="truncate text-[11px] uppercase tracking-wide text-ink-dim">{label}</div>
      <div className="truncate text-lg font-semibold tabular-nums">{value}</div>
      {sub && <div className="truncate text-[11px] text-ink-dim">{sub}</div>}
    </div>
  );
}
