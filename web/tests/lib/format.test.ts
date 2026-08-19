import { describe, expect, it } from "vitest";
import { clampPercent, feet, humanise, kcal, miles, minutes, pace, thousands } from "@/lib/format";

describe("format", () => {
  it("adds thousands separators", () => {
    expect(thousands(3599)).toBe("3,599");
    expect(thousands(999)).toBe("999");
    expect(thousands(1234567)).toBe("1,234,567");
  });

  it("rounds units to whole numbers", () => {
    expect(minutes(29.6)).toBe("30 min");
    expect(feet(232.4)).toBe("232 ft");
    expect(kcal(262.7)).toBe("263 kcal");
  });

  it("keeps two decimals for miles", () => {
    expect(miles(1.3)).toBe("1.30 mi");
    expect(miles(1.324)).toBe("1.32 mi");
  });

  describe("clampPercent", () => {
    it("bounds values to 0-100 so a progress bar cannot overflow", () => {
      expect(clampPercent(-10)).toBe(0);
      expect(clampPercent(50)).toBe(50);
      expect(clampPercent(150)).toBe(100);
    });

    it("treats non-finite input as zero", () => {
      expect(clampPercent(Number.NaN)).toBe(0);
      expect(clampPercent(Number.POSITIVE_INFINITY)).toBe(100);
    });
  });

  describe("pace", () => {
    it("renders minutes and seconds, not a decimal", () => {
      expect(pace(22.5)).toBe("22:30 /mi");
      expect(pace(20)).toBe("20:00 /mi");
    });

    it("carries a rounded 60 seconds into the minutes", () => {
      // 21.999 min would otherwise render as "21:60".
      expect(pace(21.999)).toBe("22:00 /mi");
    });

    it("renders an em dash for nonsense input", () => {
      expect(pace(0)).toBe("—");
      expect(pace(Number.NaN)).toBe("—");
    });
  });

  it("humanises snake_case keys", () => {
    expect(humanise("dog_park")).toBe("Dog park");
    expect(humanise("road")).toBe("Road");
  });
});
