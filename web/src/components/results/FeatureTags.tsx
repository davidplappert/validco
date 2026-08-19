/** Notable things found on the route: stairs, bridges, busy roads, unpaved ground. */
export default function FeatureTags({ features }: { features: string[] }) {
  if (features.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Route features">
      {features.map((feature) => (
        <li
          key={feature}
          className="rounded-full border border-line bg-ground/60 px-2 py-0.5 text-[11px] text-ink-dim"
        >
          {feature}
        </li>
      ))}
    </ul>
  );
}
