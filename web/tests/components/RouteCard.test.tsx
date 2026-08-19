import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RouteCard from "@/components/results/RouteCard";
import { hillyRoute, route } from "../fixtures";

describe("RouteCard", () => {
  it("shows the headline figures", () => {
    render(<RouteCard route={route} selected={false} onSelect={() => {}} />);
    expect(screen.getByText("30 min")).toBeInTheDocument();
    expect(screen.getByText("1.32 mi")).toBeInTheDocument();
    expect(screen.getByText("13 ft")).toBeInTheDocument();
    expect(screen.getByText("263 kcal")).toBeInTheDocument();
    expect(screen.getByText("3,333 steps")).toBeInTheDocument();
  });

  it("names the destination when there is one", () => {
    render(<RouteCard route={hillyRoute} selected={false} onSelect={() => {}} />);
    expect(screen.getByText("Out to Alta Plaza Park")).toBeInTheDocument();
  });

  it("falls back to a generic title when there is no destination", () => {
    render(<RouteCard route={route} selected={false} onSelect={() => {}} />);
    expect(screen.getByText("Neighbourhood loop")).toBeInTheDocument();
  });

  it("lists the streets walked", () => {
    render(<RouteCard route={route} selected={false} onSelect={() => {}} />);
    expect(screen.getByText(/North Main Street/)).toBeInTheDocument();
  });

  describe("fit badge", () => {
    it("reads as a great fit at a high score", () => {
      render(<RouteCard route={route} selected={false} onSelect={() => {}} />);
      expect(screen.getByText("Great fit")).toBeInTheDocument();
    });

    it("reads as challenging at a middling score", () => {
      render(<RouteCard route={hillyRoute} selected={false} onSelect={() => {}} />);
      expect(screen.getByText("Challenging")).toBeInTheDocument();
    });
  });

  describe("expansion", () => {
    it("hides the health detail when collapsed", () => {
      render(<RouteCard route={route} selected={false} onSelect={() => {}} />);
      expect(screen.queryByText(/Weekly activity target/i)).not.toBeInTheDocument();
      expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
    });

    it("reveals the health detail when selected", () => {
      render(<RouteCard route={route} selected onSelect={() => {}} />);
      expect(screen.getByText(/Weekly activity target/i)).toBeInTheDocument();
      expect(screen.getByText(/Daily steps/i)).toBeInTheDocument();
      expect(screen.getByText(/Joint loading/i)).toBeInTheDocument();
      expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
    });

    it("shows the suitability reasons, not just the score", () => {
      render(<RouteCard route={hillyRoute} selected onSelect={() => {}} />);
      expect(screen.getByText(/Why this fits \(score 54\/100\)/)).toBeInTheDocument();
      expect(screen.getByText(/steeper than the 5%/)).toBeInTheDocument();
    });

    it("shows route features when present", () => {
      render(<RouteCard route={hillyRoute} selected onSelect={() => {}} />);
      const features = screen.getByLabelText("Route features");
      expect(within(features).getByText("stairs")).toBeInTheDocument();
      expect(within(features).getByText("busy road")).toBeInTheDocument();
    });
  });

  it("calls onSelect when clicked", async () => {
    const onSelect = vi.fn();
    render(<RouteCard route={route} selected={false} onSelect={onSelect} />);
    await userEvent.click(screen.getByRole("button"));
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it("renders the surface mix with its labels", () => {
    render(<RouteCard route={route} selected={false} onSelect={() => {}} />);
    expect(screen.getByText(/Road \(no sidewalk mapped\) 85.7%/)).toBeInTheDocument();
    expect(screen.getByText(/Sidewalk 14.3%/)).toBeInTheDocument();
  });
});
