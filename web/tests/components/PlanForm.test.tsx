import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PlanForm, { DEFAULT_ADDRESS } from "@/components/form/PlanForm";
import { ADJUSTING_DELAY_MS, TYPING_DELAY_MS } from "@/hooks/useDebouncedValue";

/**
 * The form plans itself once your input settles, with no visible button to
 * press — one remains in the document, hidden until focused, because HTML
 * needs a submit button for Enter to work at all. Timing is therefore part of the contract, and these tests assert
 * both halves of it: that a change does eventually submit, and that the
 * keystrokes on the way there do not.
 *
 * **These run on the real clock, deliberately.** Fake timers are the obvious
 * choice and they do not work here: `userEvent` waits on promises that fake
 * timers never resolve, so every interaction hangs until the runner kills it.
 * That was verified with a two-line probe, and holds for a bare `click` on a
 * component with no timers of its own — so it is not something in this form.
 * The cost is roughly a second per test, which is a fair price for exercising
 * the debounce that actually ships rather than a mocked-out imitation of it.
 */

/** Comfortably past the longest debounce, with room for a slow CI runner. */
const AFTER_SETTLING_MS = TYPING_DELAY_MS + 400;

/** Long enough to prove nothing fired, short enough to stay inside the wait. */
const MID_TYPING_MS = Math.min(ADJUSTING_DELAY_MS, TYPING_DELAY_MS) - 150;

/** Wait out a real interval. Only ever used to prove a *negative*. */
function pause(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Empty the pre-filled address field so a test can type its own into it. */
async function clearAddress(user: ReturnType<typeof userEvent.setup>) {
  await user.clear(screen.getByLabelText(/Start address/i));
}

describe("PlanForm", () => {
  beforeEach(() => {
    // The address field queries /v1/suggest as you type. That behaviour has its
    // own tests; here it need only not reject.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ suggestions: [] }), { status: 200 })),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("plans without being asked, once typing settles", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);

    await clearAddress(user);
    await user.type(screen.getByLabelText(/Start address/i), "100 N Main St, Morton, IL");

    const age = screen.getByLabelText(/^Age$/i);
    await user.clear(age);
    await user.type(age, "33");

    const weight = screen.getByLabelText(/Weight/i);
    await user.clear(weight);
    await user.type(weight, "320");

    await waitFor(() => expect(onSubmit).toHaveBeenCalled(), {
      timeout: AFTER_SETTLING_MS,
    });
    expect(onSubmit.mock.lastCall?.[0]).toMatchObject({
      address: "100 N Main St, Morton, IL",
      minutes: 30,
      profile: { sex: "male", age: 33, weight_lb: 320 },
      preferences: { prefer_paths: true, avoid_busy_roads: true },
    });
  });

  it("coalesces the digits of a weight into a single request", async () => {
    // The case from the brief, verbatim: typing 3, then 6, then 0 must not plan
    // three walks — one for a 3 lb walker, one for 36 lb, one for 360.
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);

    const weight = screen.getByLabelText(/Weight/i);
    await user.clear(weight);
    await user.type(weight, "360");
    expect(onSubmit).not.toHaveBeenCalled();

    await waitFor(() => expect(onSubmit).toHaveBeenCalled(), {
      timeout: AFTER_SETTLING_MS,
    });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.lastCall?.[0].profile.weight_lb).toBe(360);
  });

  it("does not plan a half-typed address", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);

    await clearAddress(user);
    await user.type(screen.getByLabelText(/Start address/i), "908 N Sec");
    await pause(MID_TYPING_MS);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("never submits values the API would reject", async () => {
    // Without a submit button there is no native validation pass, so the bounds
    // declared on the inputs must be enforced before sending. A cleared age
    // would otherwise send NaN and turn an ordinary edit into an error message.
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);

    await user.clear(screen.getByLabelText(/^Age$/i));
    await pause(AFTER_SETTLING_MS);
    expect(onSubmit).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText(/^Age$/i), "33");
    await waitFor(() => expect(onSubmit).toHaveBeenCalled(), {
      timeout: AFTER_SETTLING_MS,
    });
  });

  it("does not submit an emptied address", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);
    await clearAddress(user);
    await pause(AFTER_SETTLING_MS);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("omits height entirely when the fields are cleared", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);

    await user.clear(screen.getByLabelText(/Height \(ft\)/i));
    await user.clear(screen.getByLabelText(/Height \(in\)/i));

    // Undefined rather than 0, so the API applies its documented population
    // default and flags the assumption instead of believing a zero.
    await waitFor(() => expect(onSubmit).toHaveBeenCalled(), {
      timeout: AFTER_SETTLING_MS,
    });
    const request = onSubmit.mock.lastCall?.[0];
    expect(request.profile.height_ft).toBeUndefined();
    expect(request.profile.height_in).toBeUndefined();
  });

  it("sends the height when supplied", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);
    await user.click(screen.getByRole("button", { name: "Avoid hills" }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalled(), {
      timeout: AFTER_SETTLING_MS,
    });
    expect(onSubmit.mock.lastCall?.[0].profile).toMatchObject({
      height_ft: 5,
      height_in: 10,
    });
  });

  it("submits immediately on Enter rather than waiting out the delay", async () => {
    // Someone who finishes typing and presses Enter has said they are done;
    // making them sit through the debounce reads as the app ignoring them.
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);

    await user.type(screen.getByLabelText(/Start address/i), "{Enter}");
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  describe("duration slider", () => {
    it("shows the chosen value in its label", () => {
      render(<PlanForm busy={false} onSubmit={vi.fn()} />);
      expect(screen.getByText(/How long do you have\? — 30 min/)).toBeInTheDocument();
    });
  });

  describe("preference chips", () => {
    it("starts with paths preferred and busy roads avoided", () => {
      render(<PlanForm busy={false} onSubmit={vi.fn()} />);
      expect(screen.getByRole("button", { name: "Prefer paths" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      expect(screen.getByRole("button", { name: "Avoid hills" })).toHaveAttribute(
        "aria-pressed",
        "false",
      );
    });

    it("toggles a preference and plans the new value", async () => {
      const user = userEvent.setup();
      const onSubmit = vi.fn();
      render(<PlanForm busy={false} onSubmit={onSubmit} />);
      await user.click(screen.getByRole("button", { name: "Avoid hills" }));
      expect(screen.getByRole("button", { name: "Avoid hills" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await waitFor(() => expect(onSubmit).toHaveBeenCalled(), {
        timeout: AFTER_SETTLING_MS,
      });
      expect(onSubmit.mock.lastCall?.[0].preferences.avoid_hills).toBe(true);
    });
  });

  it("says what it is doing while a plan is in flight", () => {
    // The button used to be the status indicator. With it gone, the form still
    // owes the user a statement — silence while the map redraws reads as a bug.
    render(<PlanForm busy onSubmit={vi.fn()} />);
    expect(screen.getByText(/Updating your walks/i)).toBeInTheDocument();
  });

  it("starts with a usable address already filled in", () => {
    // A default rather than a placeholder: the value is really in the field, so
    // the app plans a walk on load instead of failing validation. It is
    // Chillicothe City Hall, a public building.
    render(<PlanForm busy={false} onSubmit={vi.fn()} />);
    expect(screen.getByLabelText(/Start address/i)).toHaveValue(DEFAULT_ADDRESS);
  });

  it("does not re-plan the request the page already ran on load", async () => {
    // The page plans DEFAULT_PLAN_REQUEST itself on mount, and the debounce's
    // first emission is that same snapshot. Without the guard the app would
    // open by running two identical plans.
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);
    await pause(AFTER_SETTLING_MS);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
