import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { usePlanner } from "@/hooks/usePlanner";
import { resetConfig } from "@/lib/api";
import { planResponse } from "../fixtures";

const CONFIG = { apiBaseUrl: "https://api.test", env: "dev", version: "x", region: "us-east-1" };

const REQUEST = {
  address: "100 N Main St",
  minutes: 30,
  profile: { sex: "male" as const, age: 33, weight_lb: 320 },
  preferences: {
    prefer_paths: true,
    avoid_hills: false,
    avoid_stairs: false,
    avoid_busy_roads: true,
    prefer_green: false,
  },
};

/** Stub fetch so config.json resolves and /v1/plan returns `response`. */
function stub(response: Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/config.json")) {
        return new Response(JSON.stringify(CONFIG), { status: 200 });
      }
      return response.clone();
    }),
  );
}

describe("usePlanner", () => {
  beforeEach(() => {
    resetConfig();
    vi.spyOn(console, "debug").mockImplementation(() => {});
  });
  afterEach(() => resetConfig());

  it("starts empty and not busy", () => {
    const { result } = renderHook(() => usePlanner());
    expect(result.current.result).toBeNull();
    expect(result.current.busy).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("stores the plan and selects the first route on success", async () => {
    stub(new Response(JSON.stringify(planResponse), { status: 200 }));
    const { result } = renderHook(() => usePlanner());

    await act(async () => {
      await result.current.submit(REQUEST);
    });

    await waitFor(() => expect(result.current.result).not.toBeNull());
    expect(result.current.result?.region).toBe("pia");
    expect(result.current.selectedIndex).toBe(0);
    expect(result.current.busy).toBe(false);
  });

  it("changes the selected route", async () => {
    stub(new Response(JSON.stringify(planResponse), { status: 200 }));
    const { result } = renderHook(() => usePlanner());
    await act(async () => {
      await result.current.submit(REQUEST);
    });
    act(() => result.current.select(1));
    expect(result.current.selectedIndex).toBe(1);
  });

  it("surfaces the API's message and street suggestions on a miss", async () => {
    stub(
      new Response(
        JSON.stringify({
          error: "could not find 'Nowhere St' in any covered region",
          suggestions: { sf: ["Noe Street"], pia: [] },
        }),
        { status: 404 },
      ),
    );
    const { result } = renderHook(() => usePlanner());

    await act(async () => {
      await result.current.submit({ ...REQUEST, address: "Nowhere St" });
    });

    expect(result.current.error?.message).toMatch(/could not find/);
    expect(result.current.error?.hint).toBe("Did you mean: Noe Street?");
    expect(result.current.result).toBeNull();
  });

  it("omits the hint when the server offered no suggestions", async () => {
    stub(new Response(JSON.stringify({ error: "internal error" }), { status: 500 }));
    const { result } = renderHook(() => usePlanner());
    await act(async () => {
      await result.current.submit(REQUEST);
    });
    expect(result.current.error?.message).toBe("internal error");
    expect(result.current.error?.hint).toBeUndefined();
  });

  it("clears a previous result when a later request fails", async () => {
    stub(new Response(JSON.stringify(planResponse), { status: 200 }));
    const { result } = renderHook(() => usePlanner());
    await act(async () => {
      await result.current.submit(REQUEST);
    });
    expect(result.current.result).not.toBeNull();

    stub(new Response(JSON.stringify({ error: "nope" }), { status: 422 }));
    await act(async () => {
      await result.current.submit(REQUEST);
    });
    expect(result.current.result).toBeNull();
    expect(result.current.error).not.toBeNull();
  });

  it("resets back to the empty state", async () => {
    stub(new Response(JSON.stringify(planResponse), { status: 200 }));
    const { result } = renderHook(() => usePlanner());
    await act(async () => {
      await result.current.submit(REQUEST);
    });
    act(() => result.current.reset());
    expect(result.current.result).toBeNull();
    expect(result.current.selectedIndex).toBe(0);
  });
});
