"use client";

import Spinner from "@/components/feedback/Spinner";

/**
 * The offer to build coverage for somewhere we do not have yet.
 *
 * This replaces what used to be the app's only real dead end. The API can pull
 * any place in Overture on demand, so "we don't cover that area" is a question
 * — do you want to wait a couple of minutes? — rather than a refusal, and the
 * button is the whole point of the panel.
 *
 * The wording comes from the server's own `title`, `detail` and `action.label`
 * rather than being written here, so the client cannot promise something the
 * API is not offering.
 */
export default function AddRegionPrompt({
  title,
  detail,
  actionLabel,
  busy = false,
  onAccept,
}: {
  title: string;
  detail: string;
  actionLabel: string;
  busy?: boolean;
  onAccept: () => void;
}) {
  return (
    <div
      role="alert"
      // Named for the same reason ErrorPanel is: Next.js injects its own
      // role="alert" route announcer, so an unnamed alert is ambiguous.
      aria-label="Coverage needed"
      className="rounded-lg border border-accent/40 bg-accent/10 p-3 text-xs text-ink-dim"
    >
      <div className="font-medium text-ink">{title}</div>
      {detail && <p className="mt-1">{detail}</p>}
      <button
        type="button"
        onClick={onAccept}
        disabled={busy}
        className="mt-2.5 rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-ground transition hover:bg-accent-dim hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? <Spinner label="Starting…" /> : actionLabel}
      </button>
    </div>
  );
}
