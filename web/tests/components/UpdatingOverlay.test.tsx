import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import UpdatingOverlay, {
  MIN_VISIBLE_MS,
  SHOW_DELAY_MS,
} from "@/components/feedback/UpdatingOverlay";

/** The overlay names itself, so it is unambiguous among the page's statuses. */
function overlay() {
  return screen.queryByRole("status", { name: /updating your walks/i });
}

describe("UpdatingOverlay", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("renders nothing while idle", () => {
    render(<UpdatingOverlay active={false} />);
    expect(overlay()).toBeNull();
  });

  it("shows nothing at all for an update that finishes in 120ms", () => {
    const { rerender } = render(<UpdatingOverlay active />);

    act(() => vi.advanceTimersByTime(120));
    expect(overlay()).toBeNull();

    rerender(<UpdatingOverlay active={false} />);
    act(() => vi.advanceTimersByTime(5_000));

    // A loader that appears and vanishes inside a quarter of a second reads as
    // a glitch, not as progress.
    expect(overlay()).toBeNull();
  });

  it("appears only once the work has run past the show delay", () => {
    render(<UpdatingOverlay active />);

    act(() => vi.advanceTimersByTime(SHOW_DELAY_MS - 1));
    expect(overlay()).toBeNull();

    act(() => vi.advanceTimersByTime(1));
    expect(overlay()).not.toBeNull();
  });

  it("stays for its minimum once shown, even if the work ends immediately", () => {
    const { rerender } = render(<UpdatingOverlay active />);
    act(() => vi.advanceTimersByTime(SHOW_DELAY_MS));
    expect(overlay()).not.toBeNull();

    // Finishes 10ms after appearing. Leaving now is the same strobe the show
    // delay exists to prevent, just moved along a fraction of a second.
    act(() => vi.advanceTimersByTime(10));
    rerender(<UpdatingOverlay active={false} />);

    act(() => vi.advanceTimersByTime(MIN_VISIBLE_MS - 11));
    expect(overlay()).not.toBeNull();

    act(() => vi.advanceTimersByTime(1));
    expect(overlay()).toBeNull();
  });

  it("leaves as soon as a long update finishes, having served its minimum", () => {
    const { rerender } = render(<UpdatingOverlay active />);
    // Stepped rather than advanced in one jump: the minimum-visible clock is
    // armed by the render that makes the overlay visible, so the effects have
    // to be allowed to commit in between.
    act(() => vi.advanceTimersByTime(SHOW_DELAY_MS));
    act(() => vi.advanceTimersByTime(MIN_VISIBLE_MS));
    act(() => vi.advanceTimersByTime(2_000));
    expect(overlay()).not.toBeNull();

    rerender(<UpdatingOverlay active={false} />);
    expect(overlay()).toBeNull();
  });

  it("re-arms for the next update", () => {
    const { rerender } = render(<UpdatingOverlay active />);
    act(() => vi.advanceTimersByTime(SHOW_DELAY_MS));
    act(() => vi.advanceTimersByTime(MIN_VISIBLE_MS));
    rerender(<UpdatingOverlay active={false} />);
    expect(overlay()).toBeNull();

    rerender(<UpdatingOverlay active />);
    act(() => vi.advanceTimersByTime(SHOW_DELAY_MS - 1));
    expect(overlay()).toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(overlay()).not.toBeNull();
  });

  it("announces politely rather than as an alert", () => {
    render(<UpdatingOverlay active />);
    act(() => vi.advanceTimersByTime(SHOW_DELAY_MS));

    const node = overlay()!;
    expect(node).toHaveAttribute("aria-live", "polite");
    expect(node).toHaveAttribute("aria-busy", "true");
    // ErrorPanel owns the assertive role; a recompute in progress is not an
    // error and must not compete with one.
    expect(node).not.toHaveAttribute("role", "alert");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("dims without blocking the page underneath", () => {
    render(<UpdatingOverlay active />);
    act(() => vi.advanceTimersByTime(SHOW_DELAY_MS));
    const node = overlay()!;

    // No focus trap, no swallowed scroll, no blocked keystroke, and nothing to
    // undo on unmount — all of it falls out of not taking pointer events.
    expect(node.className).toContain("pointer-events-none");
    expect(node.className).toContain("fixed");
    // Translucent, so the last route stays faintly visible while it recomputes.
    expect(node.className).toMatch(/bg-ground\/\d+/);
    expect(document.body.style.overflow).toBe("");
  });

  it("leaves the document untouched after unmounting mid-update", () => {
    const { unmount } = render(<UpdatingOverlay active />);
    act(() => vi.advanceTimersByTime(SHOW_DELAY_MS));
    unmount();
    act(() => vi.advanceTimersByTime(5_000));
    expect(document.body.style.overflow).toBe("");
    expect(overlay()).toBeNull();
  });

  it("switches off the spinner's rotation under reduced motion", () => {
    render(<UpdatingOverlay active />);
    act(() => vi.advanceTimersByTime(SHOW_DELAY_MS));
    const card = overlay()!.firstElementChild as HTMLElement;
    expect(card.className).toContain("motion-reduce:[&_span]:animate-none");
  });

  it("honours overridden timings", () => {
    render(<UpdatingOverlay active showDelayMs={50} minVisibleMs={100} />);
    act(() => vi.advanceTimersByTime(49));
    expect(overlay()).toBeNull();
    act(() => vi.advanceTimersByTime(1));
    expect(overlay()).not.toBeNull();
  });
});
