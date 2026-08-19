import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AddRegionPrompt from "@/components/regions/AddRegionPrompt";
import RegionGate from "@/components/regions/RegionGate";
import RegionProgress from "@/components/regions/RegionProgress";
import type { RegionBuilder } from "@/hooks/useRegionBuilder";
import { regions } from "../fixtures";

/** A builder in whatever state a test needs, with no-op controls. */
function builder(overrides: Partial<RegionBuilder> = {}): RegionBuilder {
  return {
    state: "idle",
    progress: 0,
    stage: "",
    message: "",
    error: null,
    key: null,
    label: null,
    known: [],
    request: vi.fn(),
    retry: vi.fn(),
    cancel: vi.fn(),
    ...overrides,
  };
}

describe("RegionProgress", () => {
  it("puts the server's fraction into a real progress bar", () => {
    render(
      <RegionProgress
        label="Cupertino, CA"
        state="building"
        progress={0.42}
        stage="segments"
        message="Downloading streets and paths…"
      />,
    );
    const bar = screen.getByRole("progressbar", { name: "Building Cupertino, CA" });
    expect(bar).toHaveAttribute("aria-valuenow", "42");
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
  });

  it("announces itself politely, without stealing focus", () => {
    render(
      <RegionProgress
        label="Cupertino, CA"
        state="building"
        progress={0.1}
        stage="graph"
        message=""
      />,
    );
    const status = screen.getByRole("status", { name: "Building coverage" });
    expect(status).toHaveAttribute("aria-live", "polite");
  });

  it("shows the area, the server's message and the percentage", () => {
    render(
      <RegionProgress
        label="Cupertino, CA"
        state="building"
        progress={0.75}
        stage="terrain"
        message="Sampling elevation…"
      />,
    );
    expect(screen.getByText(/Adding Cupertino, CA/)).toBeInTheDocument();
    expect(screen.getByText("Sampling elevation…")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
  });

  it("falls back to the stage when the server sent no message", () => {
    render(
      <RegionProgress
        label="Cupertino, CA"
        state="building"
        progress={0}
        stage="addresses"
        message=""
      />,
    );
    expect(screen.getByText("Downloading addresses")).toBeInTheDocument();
  });

  it("says the wait is a one-off", () => {
    render(
      <RegionProgress
        label="Cupertino, CA"
        state="building"
        progress={0.2}
        stage="graph"
        message="Working"
      />,
    );
    expect(screen.getByText(/runs once for this area/i)).toBeInTheDocument();
    expect(screen.getByText(/loads instantly/i)).toBeInTheDocument();
  });

  it("clamps a nonsense fraction rather than drawing past the end", () => {
    render(<RegionProgress label="X" state="building" progress={4} stage="pack" message="" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });
});

describe("AddRegionPrompt", () => {
  it("renders the server's wording and calls back when accepted", async () => {
    const onAccept = vi.fn();
    render(
      <AddRegionPrompt
        title="We don't have walking data for that area yet"
        detail="We can pull it from Overture Maps now — it takes a minute or two."
        actionLabel="Add this area"
        onAccept={onAccept}
      />,
    );
    expect(screen.getByText(/don't have walking data/i)).toBeInTheDocument();
    expect(screen.getByText(/Overture Maps now/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Add this area" }));
    expect(onAccept).toHaveBeenCalledTimes(1);
  });

  it("is a named alert, so it is not confused with the route announcer", () => {
    render(<AddRegionPrompt title="t" detail="d" actionLabel="Add" onAccept={vi.fn()} />);
    expect(screen.getByRole("alert", { name: "Coverage needed" })).toBeInTheDocument();
  });

  it("disables the button while the request is starting", () => {
    render(<AddRegionPrompt title="t" detail="d" actionLabel="Add" busy onAccept={vi.fn()} />);
    expect(screen.getByRole("button")).toBeDisabled();
  });
});

describe("RegionGate", () => {
  const notCovered = {
    message: "no coverage for 'Cupertino, CA'",
    hint: "We can pull it from Overture Maps now — it takes a minute or two.",
    code: "region_not_covered",
    title: "We don't have walking data for that area yet",
    action: { kind: "add_region" as const, label: "Add this area", place: "Cupertino, CA" },
  };

  it("renders nothing when there is nothing wrong", () => {
    const { container } = render(
      <RegionGate
        error={null}
        builder={builder()}
        regions={regions}
        onAddRegion={vi.fn()}
        onRetryRegion={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("offers to build an area the API does not cover", async () => {
    const onAddRegion = vi.fn();
    render(
      <RegionGate
        error={notCovered}
        builder={builder()}
        regions={regions}
        onAddRegion={onAddRegion}
        onRetryRegion={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Add this area" }));
    expect(onAddRegion).toHaveBeenCalledWith(notCovered.action);
    // No dead-end error panel alongside the offer.
    expect(screen.queryByRole("alert", { name: "Planning error" })).not.toBeInTheDocument();
  });

  it("shows progress instead of the error once the build is under way", () => {
    render(
      <RegionGate
        error={notCovered}
        builder={builder({
          state: "building",
          label: "Cupertino, CA",
          progress: 0.5,
          stage: "graph",
          message: "Building the walking network…",
        })}
        regions={regions}
        onAddRegion={vi.fn()}
        onRetryRegion={vi.fn()}
      />,
    );
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
    expect(screen.queryByRole("button", { name: "Add this area" })).not.toBeInTheDocument();
  });

  it("shows nothing for region_building until the watch takes over", () => {
    const { container } = render(
      <RegionGate
        error={{
          message: "still building",
          code: "region_building",
          action: { kind: "poll_region", label: "Watch progress", key: "cupertino_ca" },
        }}
        builder={builder()}
        regions={regions}
        onAddRegion={vi.fn()}
        onRetryRegion={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("offers a retry when the API reports a previous build failed", async () => {
    const onRetryRegion = vi.fn();
    const action = { kind: "retry_region" as const, label: "Try again", key: "cupertino_ca" };
    render(
      <RegionGate
        error={{
          message: "previously failed",
          code: "region_build_failed",
          title: "We couldn't prepare that area",
          action,
        }}
        builder={builder()}
        regions={regions}
        onAddRegion={vi.fn()}
        onRetryRegion={onRetryRegion}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetryRegion).toHaveBeenCalledWith(action);
  });

  it("offers a retry when the build we were watching fails", async () => {
    const onRetryRegion = vi.fn();
    render(
      <RegionGate
        error={null}
        builder={builder({ state: "failed", error: "Overture has no streets there." })}
        regions={regions}
        onAddRegion={vi.fn()}
        onRetryRegion={onRetryRegion}
      />,
    );
    expect(screen.getByText("Overture has no streets there.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetryRegion).toHaveBeenCalledWith(null);
  });

  it("falls back to the ordinary error panel, listing areas already added", () => {
    render(
      <RegionGate
        error={{ message: "internal error" }}
        builder={builder({ known: [{ key: "cupertino_ca", label: "Cupertino, CA", addedAt: 1 }] })}
        regions={regions}
        onAddRegion={vi.fn()}
        onRetryRegion={vi.fn()}
      />,
    );
    const alert = screen.getByRole("alert", { name: "Planning error" });
    expect(alert).toHaveTextContent("internal error");
    expect(alert).toHaveTextContent("Areas you’ve added: Cupertino, CA");
  });
});
