"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { SUGGEST_DELAY_MS, useDebouncedValue } from "@/hooks/useDebouncedValue";
import { suggestAddresses, type AddressSuggestion } from "@/lib/suggest";
import { controlClass } from "./Field";

/**
 * An address field with a completion list, over `GET /v1/suggest`.
 *
 * A superset of {@link TextInput}: every `<input>` attribute passes through, so
 * it drops into a `Field` in place of one. The value is controlled the way
 * `NumberInput` controls its own — `value` plus `onValueChange` — because a
 * combobox has two distinct ways of changing (typing and choosing) and the
 * parent wants to tell them apart.
 *
 * Three behaviours are deliberate and load-bearing:
 *
 * **Typing is never blocked.** The list is an offer, not a gate. A failed
 * suggest request produces no dropdown and no error — the user can always type
 * the whole address, and a red box because an *optional* convenience failed
 * would be strictly worse than silence.
 *
 * **Stale responses can never win.** Requests are cheap enough to overlap, so
 * "cal" and "califo" can be in flight at once and arrive in either order. Every
 * response is checked against a sequence counter before it is allowed to touch
 * state, and the previous request is aborted on the way out. The counter is the
 * real guard: an abort is a request to stop, not a guarantee one has.
 *
 * **It is usable without a mouse.** Arrow keys move, Enter chooses, Escape
 * dismisses, Tab commits whatever is highlighted and moves on. A dropdown you
 * can only click is a dropdown a keyboard or screen-reader user cannot use at
 * all.
 */
export interface AddressAutocompleteProps extends Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "value" | "onChange" | "onSelect"
> {
  value: string;
  /** Every keystroke, immediately — this component never withholds typing. */
  onValueChange: (value: string) => void;
  /**
   * A suggestion was chosen.
   *
   * Carries the whole suggestion rather than just its text, so the parent can
   * plan from `lat`/`lon` directly and skip a geocode round trip. Note this
   * shadows the DOM's own `onSelect` (text-selection) event, which no caller
   * here wants.
   */
  onSelect?: (suggestion: AddressSuggestion) => void;
  /** Restrict completions to one region key. */
  region?: string;
  /** How many completions to ask for. The API caps at 10. */
  limit?: number;
  /** Below this many characters, almost every street matches. Mirrors the API. */
  minChars?: number;
  /** Overridable for tests; defaults to the autocomplete delay. */
  delayMs?: number;
}

export default function AddressAutocomplete({
  value,
  onValueChange,
  onSelect,
  region,
  limit = 6,
  minChars = 2,
  delayMs = SUGGEST_DELAY_MS,
  className,
  ...rest
}: AddressAutocompleteProps) {
  const baseId = useId();
  const listId = `${baseId}-listbox`;
  const optionId = (index: number) => `${baseId}-option-${index}`;

  const [options, setOptions] = useState<AddressSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  // Only a keystroke arms the dropdown. Without this the pre-filled default
  // address would fire a suggest request on page load, and choosing a
  // suggestion would immediately re-query for the text it just inserted.
  const [typed, setTyped] = useState(false);

  const query = useDebouncedValue(typed ? value.trim() : "", delayMs);

  /** Monotonic request id; only the newest response may touch state. */
  const sequence = useRef(0);

  useEffect(() => {
    if (query.length < minChars) {
      setOptions([]);
      setOpen(false);
      setActive(-1);
      return;
    }

    const id = ++sequence.current;
    const controller = new AbortController();

    suggestAddresses(query, { limit, region, signal: controller.signal })
      .then((found) => {
        if (id !== sequence.current) return;
        setOptions(found);
        setActive(-1);
        setOpen(found.length > 0);
      })
      .catch(() => {
        // Aborts, network failures and 5xx all mean the same thing here: no
        // dropdown. Autocomplete is optional; typing is not.
        if (id !== sequence.current) return;
        setOptions([]);
        setOpen(false);
      });

    return () => controller.abort();
  }, [query, limit, region, minChars]);

  const choose = useCallback(
    (suggestion: AddressSuggestion) => {
      setTyped(false);
      setOpen(false);
      setActive(-1);
      setOptions([]);
      onValueChange(suggestion.value);
      onSelect?.(suggestion);
    },
    [onSelect, onValueChange],
  );

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "ArrowDown") {
        if (options.length === 0) return;
        event.preventDefault();
        if (!open) {
          setOpen(true);
          setActive(0);
          return;
        }
        setActive((index) => (index + 1) % options.length);
        return;
      }

      if (event.key === "ArrowUp") {
        if (!open || options.length === 0) return;
        event.preventDefault();
        setActive((index) => (index <= 0 ? options.length - 1 : index - 1));
        return;
      }

      if (event.key === "Enter") {
        if (!open) return;
        // Only swallow Enter when it means "take this suggestion". With the
        // list open but nothing highlighted, Enter still belongs to the form.
        if (active >= 0 && options[active]) {
          event.preventDefault();
          choose(options[active]);
        } else {
          setOpen(false);
        }
        return;
      }

      if (event.key === "Escape") {
        if (!open) return;
        event.preventDefault();
        setOpen(false);
        setActive(-1);
        return;
      }

      if (event.key === "Tab") {
        // Commit the highlighted option, but never preventDefault: Tab has to
        // keep moving focus or the field becomes a trap.
        if (open && active >= 0 && options[active]) choose(options[active]);
        else setOpen(false);
      }
    },
    [active, choose, open, options],
  );

  return (
    <div className="relative">
      <input
        {...rest}
        type="text"
        value={value}
        onChange={(event) => {
          setTyped(true);
          onValueChange(event.target.value);
        }}
        onKeyDown={handleKeyDown}
        onBlur={(event) => {
          setOpen(false);
          rest.onBlur?.(event);
        }}
        role="combobox"
        aria-expanded={open}
        // Only while the listbox exists. Pointing at an absent id is a
        // dangling IDREF, and a screen reader resolving it finds nothing.
        aria-controls={open ? listId : undefined}
        aria-autocomplete="list"
        aria-haspopup="listbox"
        aria-activedescendant={open && active >= 0 ? optionId(active) : undefined}
        // The browser's own address autofill would cover this list with a
        // second, unrelated one.
        autoComplete="off"
        // 16px below `sm`: iOS zooms the whole page when a focused input's text
        // is smaller than that, and a zoomed viewport is very hard to get back
        // out of one-handed. Above `sm` the panel is on a device that does not
        // do this, so the field matches every other control again.
        //
        // The `!` is load-bearing, not decoration. `controlClass` already sets
        // `text-sm`, and Tailwind emits `.text-base` *before* `.text-sm` in the
        // stylesheet — so a plain `text-base` here loses the cascade and ships
        // a 14px field that zooms on every iPhone. Verified against the
        // compiled CSS in both directions.
        className={`${controlClass} text-base! sm:text-sm! ${className ?? ""}`}
      />

      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label="Address suggestions"
          // Static in the flow below `sm` and floating from `sm` up. The panel
          // is a scrolling bottom sheet on a phone, so an absolutely positioned
          // list is clipped by it exactly when the keyboard is up and there is
          // least room; in the flow it simply extends the sheet's own scroll.
          // On a wider screen there is room to float, and pushing the rest of
          // the form down on every keystroke would be worse.
          className={
            "z-30 mt-1 max-h-64 w-full overflow-y-auto overscroll-contain rounded-lg " +
            "border border-line bg-surface shadow-lg shadow-black/40 " +
            "sm:absolute sm:left-0 sm:right-0 sm:top-full"
          }
        >
          {options.map((suggestion, index) => (
            <li
              key={optionId(index)}
              id={optionId(index)}
              role="option"
              aria-selected={index === active}
              // Stop the input from blurring before the click lands; a blur
              // closes the list and the click would hit nothing.
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(suggestion)}
              onMouseEnter={() => setActive(index)}
              // 44px is the smallest reliable thumb target; the rows are the
              // whole reason this exists on a phone.
              className={
                "flex min-h-11 cursor-pointer items-center gap-2 border-b border-line/50 " +
                "px-3 py-2.5 text-sm last:border-b-0 " +
                (index === active ? "bg-accent/20 text-ink" : "text-ink")
              }
            >
              <span className="min-w-0 flex-1 truncate">{suggestion.label}</span>
              {suggestion.kind === "street" && (
                <span className="shrink-0 text-[10px] uppercase tracking-wide text-ink-dim">
                  street
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
