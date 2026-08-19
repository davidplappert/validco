"use client";

import { useEffect, useRef, useState } from "react";
import Spinner from "./Spinner";

/**
 * How long a recompute must run before it is worth telling anyone about.
 *
 * Under roughly a quarter of a second, a change reads as instant; a loader that
 * appears and vanishes inside that window is pure flicker, and flicker is read
 * as breakage. A Morton 30-minute plan comes back in ~4 ms and must show
 * nothing at all.
 */
export const SHOW_DELAY_MS = 250;

/**
 * Once shown, the minimum time it stays.
 *
 * Without this, a plan that lands at 260 ms flashes the overlay for 10 ms —
 * which is the strobe the show-delay was meant to prevent, moved along by a
 * fraction of a second rather than removed.
 */
export const MIN_VISIBLE_MS = 400;

/** idle: nothing on screen. holding: shown, minimum not yet served. shown: free to leave. */
type Phase = "idle" | "holding" | "shown";

/**
 * The full-screen busy state for a plan that is recomputing as the user types.
 *
 * Two independent timers, because "don't flash" has two halves and only one of
 * them is a delay: work must run for `showDelayMs` before anything appears, and
 * once something has appeared it stays for `minVisibleMs` regardless of when
 * the work finished. Either alone still strobes.
 *
 * It is a scrim, not a screen. The last result stays faintly visible underneath
 * because on an auto-updating form the overlay is on screen every few seconds,
 * and blanking the map each time makes a working app look like it is crashing
 * and recovering. `pointer-events-none` is what makes the rest of the promises
 * here true for free: it cannot trap focus, cannot swallow a scroll, cannot
 * block the next keystroke, and nothing has to be undone on unmount.
 *
 * The role is `status`, not `alert`: an in-progress update is polite
 * information, and this app already has one assertive live region — `ErrorPanel`
 * — which is named for exactly that reason. It carries an `aria-label` on the
 * same grounds, since the page has several `role="status"` lines and an
 * unnamed one is ambiguous to a screen-reader user and to a test.
 */
export default function UpdatingOverlay({
  active,
  label = "Updating your walks…",
  showDelayMs = SHOW_DELAY_MS,
  minVisibleMs = MIN_VISIBLE_MS,
}: {
  /** True while a recompute is in flight. */
  active: boolean;
  label?: string;
  showDelayMs?: number;
  minVisibleMs?: number;
}) {
  const [phase, setPhase] = useState<Phase>("idle");

  // Read inside a timer callback that fires after `active` may have changed.
  const activeNow = useRef(active);
  activeNow.current = active;

  useEffect(() => {
    if (active) {
      if (phase !== "idle") return;
      const timer = setTimeout(() => setPhase("holding"), showDelayMs);
      // The work finishing before the delay elapses clears this timer and
      // leaves the phase at idle — nothing is ever rendered.
      return () => clearTimeout(timer);
    }
    // Already past its minimum, so it can leave the moment the work is done.
    if (phase === "shown") setPhase("idle");
  }, [active, phase, showDelayMs]);

  useEffect(() => {
    if (phase !== "holding") return;
    const timer = setTimeout(() => {
      setPhase(activeNow.current ? "shown" : "idle");
    }, minVisibleMs);
    return () => clearTimeout(timer);
  }, [phase, minVisibleMs]);

  if (phase === "idle") return null;

  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label={label}
      className={
        "pointer-events-none fixed inset-0 z-40 flex items-center justify-center " +
        "bg-ground/55 backdrop-blur-[1px]"
      }
    >
      <div
        // `animate-fade-up` is already switched off under reduced motion in
        // globals.css; the spinner's rotation is switched off here, since it
        // lives inside the shared Spinner component.
        className={
          "animate-fade-up motion-reduce:[&_span]:animate-none flex items-center gap-3 " +
          "rounded-xl border border-line bg-surface/95 px-4 py-3 text-sm text-ink shadow-xl shadow-black/40"
        }
      >
        <Spinner label={label} />
      </div>
    </div>
  );
}
