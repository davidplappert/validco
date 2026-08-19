import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import {
  STORAGE_KEY,
  loadKnownRegions,
  pollDelayMs,
  useRegionBuilder,
} from "@/hooks/useRegionBuilder";
import { resetConfig } from "@/lib/api";
import type { RegionState } from "@/lib/types";

const CONFIG = { apiBaseUrl: "https://api.test", env: "dev", version: "x", region: "us-east-1" };

/** One call the hook made, with the fake clock's reading at the time. */
interface Call {
  method: string;
  url: string;
  at: number;
}

function region(overrides: Partial<RegionState> = {}): RegionState {
  return {
    key: "cupertino_ca",
    label: "Cupertino, CA",
    state: "building",
    progress: 0.4,
    stage: "segments",
    message: "Downloading streets and paths…",
    ...overrides,
  };
}

/**
 * Stub the network.
 *
 * `gets` is consumed one entry per poll and the last entry repeats, which is
 * what lets a test describe a build as the sequence of states the server would
 * report rather than as a pile of mock wiring.
 */
function stubFetch(options: {
  post?: RegionState | (() => never);
  gets?: RegionState[];
  getStatus?: number;
  postStatus?: number;
}): Call[] {
  const calls: Call[] = [];
  let polls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/config.json")) {
        return new Response(JSON.stringify(CONFIG), { status: 200 });
      }
      const method = init?.method ?? "GET";
      calls.push({ method, url, at: Date.now() });

      if (method === "POST") {
        return new Response(JSON.stringify(options.post ?? region({ state: "pending", progress: 0 })), {
          status: options.postStatus ?? 202,
        });
      }
      if (method === "DELETE") {
        return new Response(JSON.stringify({ key: "cupertino_ca", cleared: true }), { status: 200 });
      }
      if (options.getStatus && options.getStatus >= 400) {
        return new Response(JSON.stringify({ error: "gone", code: "not_found" }), {
          status: options.getStatus,
        });
      }
      const states = options.gets ?? [region()];
      const next = states[Math.min(polls, states.length - 1)];
      polls += 1;
      return new Response(JSON.stringify(next), { status: 200 });
    }),
  );
  return calls;
}

/** Let every pending promise settle, advancing the fake clock by `ms`. */
async function tick(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("pollDelayMs", () => {
  it("stays tight while a fast answer is still plausible", () => {
    expect(pollDelayMs(0)).toBe(1_000);
    expect(pollDelayMs(9_999)).toBe(1_000);
  });

  it("widens as the wait goes on, and caps", () => {
    expect(pollDelayMs(10_000)).toBe(2_000);
    expect(pollDelayMs(30_000)).toBe(4_000);
    expect(pollDelayMs(60_000)).toBe(5_000);
    expect(pollDelayMs(10 * 60_000)).toBe(5_000);
  });
});

describe("useRegionBuilder", () => {
  beforeEach(() => {
    resetConfig();
    window.localStorage.clear();
    vi.useFakeTimers();
    vi.spyOn(console, "debug").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.useRealTimers();
    resetConfig();
    window.localStorage.clear();
  });

  it("starts idle", () => {
    stubFetch({});
    const { result } = renderHook(() => useRegionBuilder());
    expect(result.current.state).toBe("idle");
    expect(result.current.progress).toBe(0);
    expect(result.current.known).toEqual([]);
  });

  it("requests a build, polls it, and reports it ready", async () => {
    const onReady = vi.fn();
    const calls = stubFetch({
      post: region({ state: "pending", progress: 0.02, stage: "queued", message: "Queued." }),
      gets: [
        region({ progress: 0.3 }),
        region({ progress: 0.8, stage: "graph" }),
        region({ state: "ready", progress: 1, stage: "ready", message: "Ready." }),
      ],
    });
    const { result } = renderHook(() => useRegionBuilder({ onReady }));

    act(() => result.current.request("Cupertino, CA"));
    await tick();
    expect(result.current.state).toBe("pending");
    expect(result.current.key).toBe("cupertino_ca");
    expect(result.current.label).toBe("Cupertino, CA");

    await tick(1_000);
    expect(result.current.state).toBe("building");
    expect(result.current.progress).toBe(0.3);
    expect(result.current.stage).toBe("segments");

    await tick(2_000);
    expect(result.current.state).toBe("ready");
    expect(result.current.progress).toBe(1);
    expect(onReady).toHaveBeenCalledTimes(1);
    expect(onReady.mock.calls[0][0].key).toBe("cupertino_ca");

    expect(calls[0].method).toBe("POST");
    expect(calls.filter((call) => call.method === "GET")).toHaveLength(3);
    // Polling stops the moment it is ready rather than continuing forever.
    await tick(30_000);
    expect(calls.filter((call) => call.method === "GET")).toHaveLength(3);
  });

  it("sends coordinates when no place name is available", async () => {
    const calls = stubFetch({ post: region({ state: "ready", progress: 1 }) });
    const { result } = renderHook(() => useRegionBuilder());
    act(() => result.current.request({ lat: 37.33, lon: -122.03 }));
    await tick();
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body))).toEqual({
      lat: 37.33,
      lon: -122.03,
    });
    expect(calls[0].url).toBe("https://api.test/v1/regions");
  });

  it("reports a build that fails, with the server's reason", async () => {
    stubFetch({
      gets: [region({ state: "failed", progress: 0.5, error: "Overture has no streets there." })],
    });
    const { result } = renderHook(() => useRegionBuilder());

    act(() => result.current.request("Nowhere"));
    await tick();
    await tick(1_000);

    expect(result.current.state).toBe("failed");
    expect(result.current.error).toBe("Overture has no streets there.");
    expect(loadKnownRegions()).toEqual([]);
  });

  it("reports a request that never starts", async () => {
    stubFetch({ postStatus: 500 });
    const { result } = renderHook(() => useRegionBuilder());
    act(() => result.current.request("Cupertino, CA"));
    await tick();
    expect(result.current.state).toBe("failed");
    expect(result.current.error).toBeTruthy();
  });

  it("backs off: 1s for the first ten seconds, then 2s, 4s and 5s", async () => {
    const calls = stubFetch({ gets: [region()] });
    const { result } = renderHook(() => useRegionBuilder());
    act(() => result.current.request("Cupertino, CA"));
    await tick();

    await tick(120_000);

    const polls = calls.filter((call) => call.method === "GET").map((call) => call.at);
    const gaps: number[] = [];
    for (let i = 1; i < polls.length; i += 1) gaps.push(polls[i] - polls[i - 1]);

    expect(gaps.slice(0, 9)).toEqual(Array(9).fill(1_000));
    // The first gap past the ten-second mark is the widened one.
    expect(gaps.filter((gap) => gap === 2_000).length).toBeGreaterThan(5);
    expect(gaps).toContain(4_000);
    expect(gaps.at(-1)).toBe(5_000);
    // Roughly 40 polls over two minutes, against 120 at a flat one second.
    expect(polls.length).toBeLessThan(50);
  });

  it("tolerates a dropped poll but gives up on a run of them", async () => {
    const calls = stubFetch({ getStatus: 502 });
    const { result } = renderHook(() => useRegionBuilder());
    act(() => result.current.request("Cupertino, CA"));
    await tick();

    await tick(1_000);
    expect(result.current.state).toBe("pending");
    await tick(2_000);

    expect(result.current.state).toBe("failed");
    expect(calls.filter((call) => call.method === "GET")).toHaveLength(3);
  });

  it("stops polling when unmounted", async () => {
    const calls = stubFetch({ gets: [region()] });
    const { result, unmount } = renderHook(() => useRegionBuilder());
    act(() => result.current.request("Cupertino, CA"));
    await tick();
    await tick(1_000);
    const before = calls.length;

    unmount();
    await tick(60_000);

    expect(calls).toHaveLength(before);
    // Nothing is left on the clock, either.
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stops polling when cancelled", async () => {
    const calls = stubFetch({ gets: [region()] });
    const { result } = renderHook(() => useRegionBuilder());
    act(() => result.current.request("Cupertino, CA"));
    await tick();
    await tick(1_000);
    const before = calls.length;

    act(() => result.current.cancel());
    expect(result.current.state).toBe("idle");
    await tick(60_000);
    expect(calls).toHaveLength(before);
  });

  it("watches a build somebody else started, without requesting it again", async () => {
    const calls = stubFetch({
      gets: [region({ progress: 0.6 }), region({ state: "ready", progress: 1 })],
    });
    const { result } = renderHook(() => useRegionBuilder());

    act(() => result.current.request({ key: "cupertino_ca" }));
    await tick();

    expect(calls.every((call) => call.method === "GET")).toBe(true);
    expect(result.current.progress).toBe(0.6);
    expect(result.current.label).toBe("Cupertino, CA");
  });

  it("clears the failed build before retrying it", async () => {
    const calls = stubFetch({
      gets: [region({ state: "failed", error: "timed out" }), region({ state: "ready", progress: 1 })],
    });
    const { result } = renderHook(() => useRegionBuilder());
    act(() => result.current.request("Cupertino, CA"));
    await tick();
    await tick(1_000);
    expect(result.current.state).toBe("failed");

    act(() => result.current.retry());
    await tick();

    expect(calls.map((call) => call.method)).toEqual(["POST", "GET", "DELETE", "POST"]);
    expect(result.current.error).toBeNull();
  });

  it("remembers ready areas across mounts", async () => {
    stubFetch({ post: region({ state: "ready", progress: 1 }) });
    const first = renderHook(() => useRegionBuilder());
    act(() => first.result.current.request("Cupertino, CA"));
    await tick();
    expect(first.result.current.known).toEqual([
      { key: "cupertino_ca", label: "Cupertino, CA", addedAt: expect.any(Number) },
    ]);
    first.unmount();

    const second = renderHook(() => useRegionBuilder());
    await tick();
    expect(second.result.current.known.map((entry) => entry.label)).toEqual(["Cupertino, CA"]);
  });

  it("does not record the same area twice", async () => {
    stubFetch({ post: region({ state: "ready", progress: 1 }) });
    const { result } = renderHook(() => useRegionBuilder());
    act(() => result.current.request("Cupertino, CA"));
    await tick();
    act(() => result.current.request("Cupertino, CA"));
    await tick();
    expect(result.current.known).toHaveLength(1);
  });

  it("keeps working when localStorage throws, as it does in private mode", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    stubFetch({ post: region({ state: "ready", progress: 1 }) });

    const { result } = renderHook(() => useRegionBuilder());
    expect(result.current.known).toEqual([]);

    act(() => result.current.request("Cupertino, CA"));
    await tick();

    expect(result.current.state).toBe("ready");
    expect(result.current.known).toHaveLength(1);
  });

  it("ignores stored junk rather than throwing on it", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not json");
    expect(loadKnownRegions()).toEqual([]);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([{ nope: true }, { key: "a", label: "A" }]));
    expect(loadKnownRegions()).toEqual([{ key: "a", label: "A" }]);
  });
});
