import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, getRegions, loadConfig, planWalk, resetConfig } from "@/lib/api";
import { planResponse, regions } from "../fixtures";

const CONFIG = { apiBaseUrl: "https://api.test", env: "dev", version: "abc", region: "us-east-1" };

/** Build a fetch stub that answers config.json and one API path. */
function stubFetch(handler: (url: string, init?: RequestInit) => Response) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/config.json")) {
      return new Response(JSON.stringify(CONFIG), { status: 200 });
    }
    return handler(url, init);
  });
}

describe("api client", () => {
  beforeEach(() => {
    resetConfig();
    vi.spyOn(console, "debug").mockImplementation(() => {});
  });
  afterEach(() => resetConfig());

  it("reads the API base URL from config.json", async () => {
    vi.stubGlobal("fetch", stubFetch(() => new Response("{}", { status: 200 })));
    await expect(loadConfig()).resolves.toMatchObject({ apiBaseUrl: "https://api.test" });
  });

  it("fetches config.json only once across many calls", async () => {
    const fetchMock = stubFetch(
      () => new Response(JSON.stringify({ regions, default: "sf", attribution: "" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getRegions();
    await getRegions();

    const configCalls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/config.json"));
    expect(configCalls).toHaveLength(1);
  });

  it("posts the plan request to the configured base URL", async () => {
    const fetchMock = stubFetch(
      () => new Response(JSON.stringify(planResponse), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await planWalk({
      address: "100 N Main St",
      minutes: 30,
      profile: { sex: "male", age: 33, weight_lb: 320 },
      preferences: {
        prefer_paths: true,
        avoid_hills: false,
        avoid_stairs: false,
        avoid_busy_roads: true,
        prefer_green: false,
      },
    });

    expect(result.region).toBe("pia");
    const [url, init] = fetchMock.mock.calls.at(-1)!;
    expect(String(url)).toBe("https://api.test/v1/plan");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body)).address).toBe("100 N Main St");
  });

  describe("errors", () => {
    it("raises ApiError carrying the server's message and status", async () => {
      vi.stubGlobal(
        "fetch",
        stubFetch(
          () => new Response(JSON.stringify({ error: "could not find that address" }), { status: 404 }),
        ),
      );
      await expect(
        planWalk({
          address: "nowhere",
          minutes: 30,
          profile: { sex: "male", age: 33, weight_lb: 200 },
          preferences: {
            prefer_paths: true, avoid_hills: false, avoid_stairs: false,
            avoid_busy_roads: true, prefer_green: false,
          },
        }),
      ).rejects.toMatchObject({ name: "ApiError", status: 404, message: "could not find that address" });
    });

    it("flattens per-region street suggestions into one list", () => {
      const error = new ApiError("nope", 404, {
        suggestions: { sf: ["Market Street", "Market Court"], pia: ["Main Street"] },
      });
      expect(error.suggestions()).toEqual(["Market Street", "Market Court", "Main Street"]);
    });

    it("returns no suggestions when the server sent none", () => {
      expect(new ApiError("nope", 500, {}).suggestions()).toEqual([]);
    });

    it("falls back to a generic message when the body has no error field", async () => {
      vi.stubGlobal("fetch", stubFetch(() => new Response("null", { status: 500 })));
      await expect(getRegions()).rejects.toMatchObject({ status: 500 });
    });
  });
});
