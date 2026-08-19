import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import AddressAutocomplete from "@/components/form/AddressAutocomplete";
import type { AddressSuggestion } from "@/lib/suggest";

// The network client is exercised by tests/lib; what matters here is what the
// combobox does with a promise that resolves, rejects, or arrives late.
vi.mock("@/lib/suggest", () => ({ suggestAddresses: vi.fn() }));
const { suggestAddresses } = await import("@/lib/suggest");
const suggest = vi.mocked(suggestAddresses);

/** Shaped like a real `/v1/suggest` item. */
function suggestion(label: string, extra: Partial<AddressSuggestion> = {}): AddressSuggestion {
  return {
    kind: "address",
    label,
    value: label,
    region: "sf",
    lat: 37.7919,
    lon: -122.4127,
    ...extra,
  };
}

const CALIFORNIA = suggestion("1100 CALIFORNIA ST, 94108");
const CALIFORNIA_2 = suggestion("1150 CALIFORNIA ST, 94108", { lat: 37.792, lon: -122.414 });
const CALGARY = suggestion("14 CALGARY ST, 94134", { lat: 37.72, lon: -122.41 });

/**
 * A parent that owns the value, the way `PlanForm` will.
 *
 * `delayMs` is zeroed by default so most cases can use real timers; the
 * debounce itself has its own test below.
 */
function Harness(props: Partial<React.ComponentProps<typeof AddressAutocomplete>> = {}) {
  const [value, setValue] = useState("");
  return (
    <AddressAutocomplete
      value={value}
      onValueChange={setValue}
      delayMs={0}
      aria-label="Start address"
      {...props}
    />
  );
}

/** Type a whole value in one change, so a case can name the exact query. */
function typeAll(text: string) {
  fireEvent.change(screen.getByRole("combobox"), { target: { value: text } });
}

describe("AddressAutocomplete", () => {
  beforeEach(() => {
    suggest.mockReset();
    suggest.mockResolvedValue([]);
  });
  afterEach(() => vi.useRealTimers());

  it("does not query for the value it was given on mount", async () => {
    render(<Harness value="908 N Second St" onValueChange={() => {}} />);
    await new Promise((resolve) => setTimeout(resolve, 20));
    // The form opens pre-filled with a default address; suggesting completions
    // for text nobody typed is a wasted request on every page load.
    expect(suggest).not.toHaveBeenCalled();
  });

  it("stays quiet until the query is long enough to mean something", async () => {
    render(<Harness minChars={2} />);
    typeAll("c");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(suggest).not.toHaveBeenCalled();
  });

  it("opens a listbox of suggestions once they arrive", async () => {
    suggest.mockResolvedValue([CALIFORNIA, CALIFORNIA_2]);
    render(<Harness />);

    expect(screen.getByRole("combobox")).toHaveAttribute("aria-expanded", "false");
    typeAll("1100 cal");

    const list = await screen.findByRole("listbox");
    expect(screen.getByRole("combobox")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("combobox")).toHaveAttribute("aria-controls", list.id);
    expect(screen.getAllByRole("option")).toHaveLength(2);
    expect(screen.getByText(CALIFORNIA.label)).toBeInTheDocument();
  });

  it("shows nothing at all when the request fails", async () => {
    suggest.mockRejectedValue(new Error("network down"));
    render(<Harness />);
    typeAll("1100 cal");

    await waitFor(() => expect(suggest).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 20));

    // Autocomplete is a convenience. Failing it must not put an error in front
    // of someone who can still type the whole address.
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("combobox")).toHaveValue("1100 cal");
  });

  it("never lets a slow response for an earlier query overwrite a newer one", async () => {
    let resolveSlow: (value: AddressSuggestion[]) => void = () => {};
    const slow = new Promise<AddressSuggestion[]>((resolve) => {
      resolveSlow = resolve;
    });
    suggest.mockImplementation((query: string) =>
      query === "cal" ? slow : Promise.resolve([CALIFORNIA]),
    );

    render(<Harness minChars={3} />);
    typeAll("cal");
    await waitFor(() => expect(suggest).toHaveBeenCalledWith("cal", expect.anything()));

    typeAll("califo");
    expect(await screen.findByText(CALIFORNIA.label)).toBeInTheDocument();

    // "cal" finally comes back, long after the user moved on.
    await act(async () => {
      resolveSlow([CALGARY]);
      await slow;
    });

    expect(screen.queryByText(CALGARY.label)).toBeNull();
    expect(screen.getByText(CALIFORNIA.label)).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(1);
  });

  it("issues one request for a burst of keystrokes", async () => {
    vi.useFakeTimers();
    suggest.mockResolvedValue([CALIFORNIA]);
    render(<Harness delayMs={180} />);
    const input = screen.getByRole("combobox");

    for (const value of ["c", "ca", "cal", "cali"]) {
      fireEvent.change(input, { target: { value } });
      act(() => vi.advanceTimersByTime(50));
    }
    expect(suggest).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(180);
    });
    expect(suggest).toHaveBeenCalledOnce();
    expect(suggest).toHaveBeenCalledWith("cali", expect.anything());
  });

  it("moves through the list with the arrow keys and chooses with Enter", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    suggest.mockResolvedValue([CALIFORNIA, CALIFORNIA_2]);
    render(<Harness onSelect={onSelect} />);

    typeAll("1100 cal");
    await screen.findByRole("listbox");
    const input = screen.getByRole("combobox");
    input.focus();

    expect(input).not.toHaveAttribute("aria-activedescendant");

    await user.keyboard("{ArrowDown}");
    const [first, second] = screen.getAllByRole("option");
    expect(input).toHaveAttribute("aria-activedescendant", first.id);
    expect(first).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowDown}");
    expect(input).toHaveAttribute("aria-activedescendant", second.id);

    await user.keyboard("{ArrowUp}");
    expect(input).toHaveAttribute("aria-activedescendant", first.id);

    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith(CALIFORNIA);
    expect(input).toHaveValue(CALIFORNIA.label);
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("wraps from the last option back to the first", async () => {
    const user = userEvent.setup();
    suggest.mockResolvedValue([CALIFORNIA, CALIFORNIA_2]);
    render(<Harness />);
    typeAll("1100 cal");
    await screen.findByRole("listbox");
    screen.getByRole("combobox").focus();

    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}");
    const [first] = screen.getAllByRole("option");
    expect(screen.getByRole("combobox")).toHaveAttribute("aria-activedescendant", first.id);
  });

  it("closes on Escape without changing what was typed", async () => {
    const user = userEvent.setup();
    suggest.mockResolvedValue([CALIFORNIA]);
    render(<Harness />);
    typeAll("1100 cal");
    await screen.findByRole("listbox");
    screen.getByRole("combobox").focus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.getByRole("combobox")).toHaveValue("1100 cal");
    expect(screen.getByRole("combobox")).toHaveAttribute("aria-expanded", "false");
  });

  it("commits the highlighted option on Tab and lets focus move on", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    suggest.mockResolvedValue([CALIFORNIA]);
    render(<Harness onSelect={onSelect} />);
    typeAll("1100 cal");
    await screen.findByRole("listbox");
    screen.getByRole("combobox").focus();

    await user.keyboard("{ArrowDown}");
    await user.keyboard("{Tab}");

    expect(onSelect).toHaveBeenCalledWith(CALIFORNIA);
    expect(screen.getByRole("combobox")).not.toHaveFocus();
  });

  it("chooses on click and reports the coordinates with the label", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    suggest.mockResolvedValue([CALIFORNIA]);
    render(<Harness onSelect={onSelect} />);
    typeAll("1100 cal");
    await screen.findByRole("listbox");

    await user.click(screen.getByRole("option", { name: new RegExp(CALIFORNIA.label, "i") }));

    // The lat/lon travel with the choice so the parent can plan without a
    // second geocode round trip.
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ label: CALIFORNIA.label, lat: 37.7919, lon: -122.4127 }),
    );
    expect(screen.getByRole("combobox")).toHaveValue(CALIFORNIA.label);
  });

  it("does not re-query the text it just inserted", async () => {
    const user = userEvent.setup();
    suggest.mockResolvedValue([CALIFORNIA]);
    render(<Harness />);
    typeAll("1100 cal");
    await screen.findByRole("listbox");
    suggest.mockClear();

    await user.click(screen.getByRole("option", { name: new RegExp(CALIFORNIA.label, "i") }));
    await new Promise((resolve) => setTimeout(resolve, 30));

    expect(suggest).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("passes the region and limit through to the endpoint", async () => {
    render(<Harness region="pia" limit={4} />);
    typeAll("main");
    await waitFor(() =>
      expect(suggest).toHaveBeenCalledWith(
        "main",
        expect.objectContaining({ region: "pia", limit: 4 }),
      ),
    );
  });

  it("keeps the input tappable and un-zoomable on a phone", async () => {
    suggest.mockResolvedValue([CALIFORNIA]);
    render(<Harness />);
    const input = screen.getByRole("combobox");

    // Below 16px iOS zooms the viewport on focus, and getting back out of that
    // one-handed is genuinely hard. The `!` matters: the shared control class
    // sets `text-sm`, and Tailwind emits `.text-base` first, so an unimportant
    // `text-base` would silently lose and ship a zooming field.
    expect(input.className).toContain("text-base!");
    expect(input.className).toContain("sm:text-sm!");

    typeAll("1100 cal");
    await screen.findByRole("listbox");
    // 44px: the smallest reliable thumb target.
    expect(screen.getAllByRole("option")[0].className).toContain("min-h-11");
  });
});
