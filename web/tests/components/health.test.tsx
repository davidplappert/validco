import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import GuidelineCard from "@/components/health/GuidelineCard";
import StepsCard from "@/components/health/StepsCard";
import Caveats from "@/components/health/Caveats";
import ProgressBar from "@/components/health/ProgressBar";
import { health } from "../fixtures";

describe("ProgressBar", () => {
  it("exposes the percentage through ARIA, not just visually", () => {
    render(<ProgressBar percent={47.6} label="Daily steps" />);
    const bar = screen.getByRole("progressbar", { name: "Daily steps" });
    expect(bar).toHaveAttribute("aria-valuenow", "48");
    expect(bar).toHaveAttribute("aria-valuemax", "100");
  });

  it("never renders past 100%", () => {
    render(<ProgressBar percent={250} label="Overachieved" />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  });
});

describe("GuidelineCard", () => {
  it("reports progress toward the WHO weekly target", () => {
    render(<GuidelineCard guideline={health.guideline_progress} />);
    expect(screen.getByText(/20% of the WHO 150 min\/week/)).toBeInTheDocument();
  });

  it("says so when the pace is too light to count", () => {
    render(
      <GuidelineCard
        guideline={{
          ...health.guideline_progress,
          counts_as_moderate: false,
          pct_of_weekly_target: 0,
        }}
      />,
    );
    expect(screen.getByText(/this pace counts as light/)).toBeInTheDocument();
  });

  it("does not add the light-pace note for a moderate walk", () => {
    render(<GuidelineCard guideline={health.guideline_progress} />);
    expect(screen.queryByText(/counts as light/)).not.toBeInTheDocument();
  });
});

describe("StepsCard", () => {
  it("reports steps against the 7,000-step target", () => {
    render(<StepsCard steps={health.steps} />);
    expect(screen.getByText(/3,333 steps/)).toBeInTheDocument();
    expect(screen.getByText(/47.6% of a 7,000-step day/)).toBeInTheDocument();
  });
});

describe("Caveats", () => {
  it("renders the API's caveats verbatim", () => {
    render(<Caveats caveats={["Estimates only.", "Not medical advice."]} />);
    expect(screen.getByText("Estimates only. Not medical advice.")).toBeInTheDocument();
  });

  it("renders nothing when there are none", () => {
    const { container } = render(<Caveats caveats={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
