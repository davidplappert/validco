"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Waiting mechanisms for a form that plans as you type.
 *
 * Once the submit button goes away, every keystroke is a potential request, and
 * the only question left is *how long to wait before believing the user meant
 * it*. There is no single right answer, because the three kinds of input in
 * this form fail in different directions:
 *
 * `TYPING_DELAY_MS` (700) — **free text, expensive request.** Someone typing
 * "908 N Second St" passes through fifteen prefixes, none of which is a real
 * address; a plan fired at "908 N Sec" is not a slow answer, it is a wrong one,
 * and it costs a geocode plus a Dijkstra over tens of thousands of nodes. The
 * cost of waiting too long is one extra beat before the map moves. The cost of
 * waiting too little is a screen full of answers to questions nobody asked. So
 * this one waits until typing has genuinely stopped — around 700 ms, comfortably
 * past the ~250 ms inter-keystroke gap of an average typist and still under the
 * one second at which a pause starts to feel like a hang.
 *
 * `ADJUSTING_DELAY_MS` (400) — **numeric and slider input.** "360 lb" is the
 * literal case in the brief: 3, then 6, then 0. Every prefix here is a *valid*
 * number, so the request is never nonsense, merely premature — 3 lb plans fine,
 * it is just not what was meant. The user is converging on a value and watching
 * it change under their hand, so the feedback loop wants to be tighter than for
 * text; 400 ms still coalesces the three digits of a weight or a drag across a
 * slider into one request, because a drag emits events every few milliseconds.
 *
 * `SUGGEST_DELAY_MS` (180) — **autocomplete keystrokes.** This request is a
 * prefix scan over an in-memory index in the same Lambda, measured in
 * microseconds, and its entire job is to feel like it is happening as you type.
 * A dropdown that lags behind the caret by half a second is worse than no
 * dropdown, because the user has already typed past whatever it is offering.
 * The delay exists only to skip the obviously-wasted calls between fast
 * keystrokes, not to wait for a pause.
 *
 * The shape of the argument is the same each time — wait long enough that
 * intermediate values do not cost anything, and no longer — but the inputs to
 * it (how expensive the call is, whether a partial value is meaningful, how
 * closely the user is watching) differ by an order of magnitude, so the answers
 * do too. One constant for all three would have to be a compromise that is
 * simultaneously too slow for the dropdown and too fast for the address.
 */

/** Free text, where a partial value is meaningless and the request is costly. */
export const TYPING_DELAY_MS = 700;

/** Numbers and sliders, where the user is converging on a value they can see. */
export const ADJUSTING_DELAY_MS = 400;

/** Autocomplete keystrokes: cheap, local, and required to feel instantaneous. */
export const SUGGEST_DELAY_MS = 180;

export interface SettledOptions<T> {
  /** Fixed wait. Ignored when `delayFor` is supplied. */
  delayMs?: number;
  /**
   * Per-change wait, given the incoming value and the one it replaced.
   *
   * This is what lets one form debounce its address field at
   * `TYPING_DELAY_MS` and its weight field at `ADJUSTING_DELAY_MS` while
   * still coalescing into a single request: the caller passes the whole form
   * snapshot and decides the delay from which part of it moved.
   */
  delayFor?: (next: T, previous: T) => number;
  /**
   * Equality test. Defaults to `Object.is`; pass a field-wise comparison for
   * object values, which are otherwise a fresh reference on every render.
   */
  isEqual?: (a: T, b: T) => boolean;
}

export interface Settled<T> {
  /** The value once it has stopped changing. */
  value: T;
  /** True while a change is waiting out its delay — drives the loader. */
  pending: boolean;
  /**
   * Increments once per delivery, including every {@link Settled.flush}.
   *
   * Consumers should key their effect on this rather than on `value`. Pressing
   * "now" twice with nothing changed in between is two deliveries of an
   * identical value, and an effect watching `value` would see one — so the
   * second press would do nothing, which is precisely what a user reaches for
   * that control to avoid.
   */
  revision: number;
  /**
   * Deliver the current input immediately and cancel anything armed.
   *
   * For the explicit "do it now" path — pressing Enter. Cancelling matters as
   * much as delivering: without it the armed timer still fires a few hundred
   * milliseconds later and submits the same value a second time.
   */
  flush: () => void;
}

/**
 * The general form: a value that lags behind its input until the input settles.
 *
 * Two behaviours are worth calling out, because both are bugs when they are
 * missing:
 *
 * 1. **A value that returns to what was already committed does not re-fire.**
 *    Typing "3600" and deleting the last character leaves the field at "360",
 *    which is exactly what the last request was for. Re-running it would burn a
 *    plan and flash the loader to produce a byte-identical answer.
 * 2. **The effect has no dependency array on purpose.** It runs after every
 *    render and compares with `isEqual`, so an object rebuilt fresh each render
 *    — which is what a form snapshot is — does not restart the timer forever.
 *    A dependency array on the value would make this hook silently never fire
 *    for exactly the caller this codebase needs it for.
 */
export function useSettledValue<T>(value: T, options: SettledOptions<T> = {}): Settled<T> {
  const [delivered, setDelivered] = useState<{ value: T; revision: number }>({
    value,
    revision: 0,
  });
  const [pending, setPending] = useState(false);

  // Read through a ref so an inline options object does not re-arm anything.
  const latestOptions = useRef(options);
  latestOptions.current = options;

  /** The live input, readable from a callback that fires later. */
  const latestValue = useRef(value);
  latestValue.current = value;

  /** The most recent input this hook has already reacted to. */
  const seen = useRef(value);
  /** The most recent value actually handed back to the caller. */
  const committed = useRef(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // The missing dependency array is the design, not an oversight.
  //
  // The rule's specific warning is that calling `setPending` with no deps can
  // loop forever. It cannot here: the effect early-returns whenever `isEqual`
  // says the value has not moved, and a setState identity is stable across
  // renders. Taking the rule's advice and passing `[value]` would break the
  // caller this hook exists for — a form snapshot is a fresh object every
  // render, so a dependency array would restart the timer on every render and
  // the debounce would never fire at all.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    const { delayMs = TYPING_DELAY_MS, delayFor, isEqual = Object.is } = latestOptions.current;
    if (isEqual(value, seen.current)) return;

    const previous = seen.current;
    seen.current = value;

    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }

    // Back to the value already in flight or already delivered: cancel, and
    // deliberately do not schedule a replacement.
    if (isEqual(value, committed.current)) {
      setPending(false);
      return;
    }

    setPending(true);
    const wait = delayFor ? delayFor(value, previous) : delayMs;
    timer.current = setTimeout(() => {
      timer.current = null;
      committed.current = value;
      setDelivered((previousDelivery) => ({
        value,
        revision: previousDelivery.revision + 1,
      }));
      setPending(false);
    }, wait);
  });

  const flush = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const current = latestValue.current;
    seen.current = current;
    committed.current = current;
    setPending(false);
    setDelivered((previousDelivery) => ({
      value: current,
      revision: previousDelivery.revision + 1,
    }));
  }, []);

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );

  return { value: delivered.value, pending, revision: delivered.revision, flush };
}

/**
 * The plain trailing debounce: `value`, but only once it has held still.
 *
 * A thin reading of {@link useSettledValue} for the common case where one
 * field has one delay and the caller does not need the pending flag.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  return useSettledValue(value, { delayMs }).value;
}
