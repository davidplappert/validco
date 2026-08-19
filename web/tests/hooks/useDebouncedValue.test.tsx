import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useEffect } from "react";
import {
  ADJUSTING_DELAY_MS,
  SUGGEST_DELAY_MS,
  TYPING_DELAY_MS,
  useDebouncedValue,
  useSettledValue,
} from "@/hooks/useDebouncedValue";

/**
 * Counts how many times the settled value actually reached a consumer.
 *
 * The point of the hook is not that it eventually returns "360" — it is that
 * everything downstream of it runs *once*. An effect keyed on the settled value
 * is exactly what a real caller does with it, so counting its runs is the
 * honest measure.
 */
function useCountedDebounce(value: string, delayMs: number, onSettled: (value: string) => void) {
  const settled = useDebouncedValue(value, delayMs);
  useEffect(() => {
    onSettled(settled);
  }, [settled, onSettled]);
}

describe("useDebouncedValue", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("returns the initial value straight away", () => {
    const { result } = renderHook(() => useDebouncedValue("360", TYPING_DELAY_MS));
    expect(result.current).toBe("360");
  });

  it("coalesces 3, 6, 0 into a single settled value", () => {
    const onSettled = vi.fn();
    const { rerender } = renderHook(
      ({ value }) => useCountedDebounce(value, ADJUSTING_DELAY_MS, onSettled),
      { initialProps: { value: "" } },
    );

    // The mount itself delivers the initial value; everything after this is
    // what the typing produced.
    expect(onSettled).toHaveBeenCalledTimes(1);

    for (const value of ["3", "36", "360"]) {
      rerender({ value });
      act(() => vi.advanceTimersByTime(100));
    }

    // Three keystrokes, 300 ms of typing, nothing fired yet.
    expect(onSettled).toHaveBeenCalledTimes(1);

    act(() => vi.advanceTimersByTime(ADJUSTING_DELAY_MS));
    expect(onSettled).toHaveBeenCalledTimes(2);
    expect(onSettled).toHaveBeenLastCalledWith("360");
  });

  it("waits out the full delay from the last keystroke, not the first", () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 400), {
      initialProps: { value: "a" },
    });

    rerender({ value: "ab" });
    act(() => vi.advanceTimersByTime(399));
    rerender({ value: "abc" });
    act(() => vi.advanceTimersByTime(399));
    expect(result.current).toBe("a");

    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe("abc");
  });

  it("does not re-fire when the value returns to what was already settled", () => {
    const onSettled = vi.fn();
    const { rerender } = renderHook(
      ({ value }) => useCountedDebounce(value, ADJUSTING_DELAY_MS, onSettled),
      { initialProps: { value: "360" } },
    );
    expect(onSettled).toHaveBeenCalledTimes(1);

    // Deleting a digit and typing it back leaves the field exactly where the
    // last request left it. Re-running it would cost a plan for no new answer.
    rerender({ value: "36" });
    act(() => vi.advanceTimersByTime(100));
    rerender({ value: "360" });
    act(() => vi.advanceTimersByTime(5_000));

    expect(onSettled).toHaveBeenCalledTimes(1);
  });

  it("delivers a genuinely new value after a detour through an old one", () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 200), {
      initialProps: { value: "360" },
    });

    rerender({ value: "36" });
    rerender({ value: "360" });
    act(() => vi.advanceTimersByTime(500));
    expect(result.current).toBe("360");

    rerender({ value: "365" });
    act(() => vi.advanceTimersByTime(200));
    expect(result.current).toBe("365");
  });
});

describe("useSettledValue", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("uses a different delay depending on what changed", () => {
    // The real reason this hook exists: one form snapshot, one request, but a
    // typed address gets more patience than a nudged number.
    const delayFor = (next: string) =>
      /^\d+$/.test(next) ? ADJUSTING_DELAY_MS : TYPING_DELAY_MS;

    const { result, rerender } = renderHook(({ value }) => useSettledValue(value, { delayFor }), {
      initialProps: { value: "" },
    });

    rerender({ value: "360" });
    act(() => vi.advanceTimersByTime(ADJUSTING_DELAY_MS - 1));
    expect(result.current.value).toBe("");
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.value).toBe("360");

    rerender({ value: "100 N Main St" });
    act(() => vi.advanceTimersByTime(ADJUSTING_DELAY_MS));
    expect(result.current.value).toBe("360");
    act(() => vi.advanceTimersByTime(TYPING_DELAY_MS - ADJUSTING_DELAY_MS));
    expect(result.current.value).toBe("100 N Main St");
  });

  it("reports pending only while a change is waiting", () => {
    const { result, rerender } = renderHook(({ value }) => useSettledValue(value, { delayMs: 300 }), {
      initialProps: { value: "a" },
    });
    expect(result.current.pending).toBe(false);

    rerender({ value: "ab" });
    expect(result.current.pending).toBe(true);

    act(() => vi.advanceTimersByTime(300));
    expect(result.current.pending).toBe(false);
    expect(result.current.value).toBe("ab");
  });

  it("clears pending when the value returns to the settled one", () => {
    const { result, rerender } = renderHook(({ value }) => useSettledValue(value, { delayMs: 300 }), {
      initialProps: { value: "a" },
    });

    rerender({ value: "ab" });
    expect(result.current.pending).toBe(true);
    rerender({ value: "a" });
    expect(result.current.pending).toBe(false);
  });

  it("re-arms rather than restarting forever for an object rebuilt each render", () => {
    // A form snapshot is a fresh object every render. With a field-wise
    // comparison the hook must settle; without one it would never fire at all.
    const isEqual = (a: { minutes: number }, b: { minutes: number }) => a.minutes === b.minutes;
    const { result, rerender } = renderHook(
      ({ minutes }) => useSettledValue({ minutes }, { delayMs: 200, isEqual }),
      { initialProps: { minutes: 30 } },
    );

    rerender({ minutes: 45 });
    // Extra renders with an equal-but-new object must not push the timer out.
    act(() => vi.advanceTimersByTime(100));
    rerender({ minutes: 45 });
    act(() => vi.advanceTimersByTime(100));

    expect(result.current.value.minutes).toBe(45);
  });

  it("orders the three delays by how expensive a premature request is", () => {
    expect(SUGGEST_DELAY_MS).toBeLessThan(ADJUSTING_DELAY_MS);
    expect(ADJUSTING_DELAY_MS).toBeLessThan(TYPING_DELAY_MS);
  });
});
