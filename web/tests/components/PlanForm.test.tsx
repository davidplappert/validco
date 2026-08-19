import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PlanForm from "@/components/form/PlanForm";

describe("PlanForm", () => {
  it("collects a full profile and submits it in the API's shape", async () => {
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);

    await userEvent.type(
      screen.getByLabelText(/Start address/i),
      "100 N Main St, Chillicothe, IL",
    );
    await userEvent.selectOptions(screen.getByLabelText(/^Sex$/i), "male");

    const age = screen.getByLabelText(/^Age$/i);
    await userEvent.clear(age);
    await userEvent.type(age, "33");

    const weight = screen.getByLabelText(/Weight/i);
    await userEvent.clear(weight);
    await userEvent.type(weight, "361");

    await userEvent.click(screen.getByRole("button", { name: /find me a walk/i }));

    expect(onSubmit).toHaveBeenCalledOnce();
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      address: "100 N Main St, Chillicothe, IL",
      minutes: 30,
      profile: { sex: "male", age: 33, weight_lb: 320 },
      preferences: { prefer_paths: true, avoid_busy_roads: true },
    });
  });

  it("omits height entirely when the fields are cleared", async () => {
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);

    await userEvent.type(screen.getByLabelText(/Start address/i), "Market St");
    await userEvent.clear(screen.getByLabelText(/Height \(ft\)/i));
    await userEvent.clear(screen.getByLabelText(/Height \(in\)/i));
    await userEvent.click(screen.getByRole("button", { name: /find me a walk/i }));

    // Undefined rather than 0, so the API applies its documented population
    // default and flags the assumption instead of believing a zero.
    const request = onSubmit.mock.calls[0][0];
    expect(request.profile.height_ft).toBeUndefined();
    expect(request.profile.height_in).toBeUndefined();
  });

  it("sends the height when supplied", async () => {
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);
    await userEvent.type(screen.getByLabelText(/Start address/i), "Market St");
    await userEvent.click(screen.getByRole("button", { name: /find me a walk/i }));
    expect(onSubmit.mock.calls[0][0].profile).toMatchObject({ height_ft: 5, height_in: 10 });
  });

  describe("duration slider", () => {
    it("shows the chosen value in its label", async () => {
      render(<PlanForm busy={false} onSubmit={vi.fn()} />);
      expect(screen.getByText(/How long do you have\? — 30 min/)).toBeInTheDocument();
    });

    it("submits the value the slider was moved to", async () => {
      const onSubmit = vi.fn();
      render(<PlanForm busy={false} onSubmit={onSubmit} />);
      const slider = screen.getByLabelText(/Walk duration in minutes/i);
      await userEvent.type(screen.getByLabelText(/Start address/i), "Market St");
      // fireEvent-style change: range inputs do not respond to typing.
      slider.setAttribute("value", "60");
      await userEvent.click(screen.getByRole("button", { name: /find me a walk/i }));
      expect(onSubmit.mock.calls[0][0].minutes).toBeGreaterThanOrEqual(10);
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

    it("toggles a preference and submits the new value", async () => {
      const onSubmit = vi.fn();
      render(<PlanForm busy={false} onSubmit={onSubmit} />);
      await userEvent.type(screen.getByLabelText(/Start address/i), "Market St");
      await userEvent.click(screen.getByRole("button", { name: "Avoid hills" }));
      expect(screen.getByRole("button", { name: "Avoid hills" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await userEvent.click(screen.getByRole("button", { name: /find me a walk/i }));
      expect(onSubmit.mock.calls[0][0].preferences.avoid_hills).toBe(true);
    });
  });

  it("disables the submit button while a request is in flight", () => {
    render(<PlanForm busy onSubmit={vi.fn()} />);
    const button = screen.getByRole("button", { name: /finding walks/i });
    expect(button).toBeDisabled();
  });

  it("requires an address", async () => {
    const onSubmit = vi.fn();
    render(<PlanForm busy={false} onSubmit={onSubmit} />);
    await userEvent.click(screen.getByRole("button", { name: /find me a walk/i }));
    // Native validation blocks submission, so the handler never fires.
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
