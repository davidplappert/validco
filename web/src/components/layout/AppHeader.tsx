/** Product name, one-line pitch, and the deployment environment badge. */
export default function AppHeader({ env = "dev" }: { env?: string }) {
  return (
    <header className="flex items-baseline justify-between gap-2">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">
          StepWise<span className="text-accent">.</span>
        </h1>
        <p className="text-[11px] text-ink-dim">
          Walking routes scored for your body, on Overture Maps data
        </p>
      </div>
      <span className="rounded-full border border-accent/40 px-2 py-0.5 text-[10px] uppercase tracking-wider text-accent">
        {env}
      </span>
    </header>
  );
}
