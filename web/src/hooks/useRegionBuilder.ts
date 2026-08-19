"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, clearRegion, getRegion, requestRegion } from "@/lib/api";
import type { RegionLifecycle, RegionState } from "@/lib/types";

/**
 * Where to build coverage. At least one of `place`, `lat`/`lon` or `key`.
 *
 * The three come from different places and behave differently: `place` and
 * coordinates are what `region_not_covered` hands back and need a POST to start
 * a build, while a bare `key` comes from `region_building` — somebody else has
 * already started it, so there is nothing to request and we only watch.
 */
export interface RegionTarget {
  place?: string;
  lat?: number;
  lon?: number;
  key?: string;
  label?: string;
}

/** Lifecycle as the UI sees it, including the two states before the API knows. */
export type RegionBuildState = "idle" | "requesting" | RegionLifecycle;

/** One coverage area this browser has seen finish, as persisted. */
export interface KnownRegion {
  key: string;
  label: string;
  addedAt: number;
}

export interface RegionBuilder {
  state: RegionBuildState;
  /** 0..1, straight from the server. */
  progress: number;
  stage: string;
  message: string;
  error: string | null;
  key: string | null;
  label: string | null;
  /** Areas this browser has already built, newest first. */
  known: KnownRegion[];
  request: (target: RegionTarget | string) => void;
  /** Clear a failed build and start it again. Defaults to the last target. */
  retry: (target?: RegionTarget) => void;
  cancel: () => void;
}

export interface RegionBuilderOptions {
  /**
   * Called once when a build reaches `ready`.
   *
   * This is how the page re-runs the plan the user originally asked for; the
   * point of building an area is the walk on the other side of it, and making
   * them retype the address to get it would waste the wait.
   */
  onReady?: (region: RegionState) => void;
}

/** One namespaced key, so clearing it clears everything this app stored. */
export const STORAGE_KEY = "stepwise.regions.v1";

/** How long we keep polling a single build before giving up, in ms. */
const MAX_POLL_MS = 10 * 60 * 1000;

/** Consecutive failed polls tolerated before the build is called lost. */
const MAX_POLL_ERRORS = 3;

/**
 * How long to wait before the next poll, given how long we have been waiting.
 *
 * A build takes one to three minutes. Polling every second throughout would be
 * roughly 150 pointless requests for one answer, so the interval widens as the
 * odds of finishing on this poll drop — while staying tight at the start, where
 * an already-built area comes back almost immediately.
 */
export function pollDelayMs(elapsedMs: number): number {
  if (elapsedMs < 10_000) return 1_000;
  if (elapsedMs < 30_000) return 2_000;
  if (elapsedMs < 60_000) return 4_000;
  return 5_000;
}

/**
 * Read the remembered regions.
 *
 * Every localStorage call is guarded: Safari's private mode throws on write,
 * some privacy extensions throw on read, and a browser that will not remember
 * anything is a smaller problem than one that shows a blank page.
 */
export function loadKnownRegions(): KnownRegion[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (entry): entry is KnownRegion =>
        Boolean(entry) &&
        typeof (entry as KnownRegion).key === "string" &&
        typeof (entry as KnownRegion).label === "string",
    );
  } catch {
    return [];
  }
}

/** Persist the list, ignoring a storage that refuses to hold it. */
function saveKnownRegions(regions: KnownRegion[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(regions));
  } catch {
    // Nothing to do: the feature degrades to "you wait again next time".
  }
}

/** The message to show for a failure, preferring whatever the server said. */
function describe(caught: unknown, fallback: string): string {
  if (caught instanceof ApiError) return caught.userDetail || caught.message;
  if (caught instanceof Error) return caught.message;
  return fallback;
}

/**
 * Owns requesting coverage for a new area and watching it get built.
 *
 * The API builds any place in Overture on demand, which turns "we don't cover
 * that" from a dead end into a wait. A wait needs to be visible, so this hook
 * tracks the server's own progress and stage rather than showing a spinner, and
 * it remembers what finished so a returning visitor never waits twice.
 */
export function useRegionBuilder(options: RegionBuilderOptions = {}): RegionBuilder {
  const [state, setState] = useState<RegionBuildState>("idle");
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const [label, setLabel] = useState<string | null>(null);
  const [known, setKnown] = useState<KnownRegion[]>([]);

  // A monotonic generation number, bumped by every new request, by cancel() and
  // by unmount. Everything async compares against it before touching state, so
  // a poll that was already in flight when the user moved on cannot resurrect a
  // finished build or set state on an unmounted component.
  const generation = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const target = useRef<RegionTarget | null>(null);
  // The latest key and label as refs as well as state, because retry() needs
  // them synchronously and setState has not necessarily landed by then.
  const keyRef = useRef<string | null>(null);
  const labelRef = useRef<string | null>(null);
  const knownRef = useRef<KnownRegion[]>([]);

  const onReady = options.onReady;
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  // Reading storage during render would differ between the server prerender and
  // the client, so it happens in an effect after mount instead.
  useEffect(() => {
    const stored = loadKnownRegions();
    knownRef.current = stored;
    setKnown(stored);
  }, []);

  const stopTimer = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const cancel = useCallback(() => {
    generation.current += 1;
    stopTimer();
    setState("idle");
    setError(null);
    setProgress(0);
    setStage("");
    setMessage("");
  }, [stopTimer]);

  // Unmount must leave nothing running: no timer, and no pending promise that
  // would call setState when it resolves.
  useEffect(() => {
    return () => {
      generation.current += 1;
      stopTimer();
    };
  }, [stopTimer]);

  // Kept off the setState updater deliberately: writing to storage inside one
  // would run twice under StrictMode's double-invoked updaters.
  const remember = useCallback((region: RegionState) => {
    const next: KnownRegion[] = [
      { key: region.key, label: region.label || region.key, addedAt: Date.now() },
      ...knownRef.current.filter((entry) => entry.key !== region.key),
    ];
    knownRef.current = next;
    saveKnownRegions(next);
    setKnown(next);
  }, []);

  /** Copy one server snapshot into the visible state. */
  const apply = useCallback((region: RegionState) => {
    setKey(region.key);
    keyRef.current = region.key;
    if (region.label) {
      setLabel(region.label);
      labelRef.current = region.label;
    }
    setState(region.state);
    setProgress(typeof region.progress === "number" ? region.progress : 0);
    setStage(region.stage ?? "");
    setMessage(region.message ?? "");
  }, []);

  /**
   * Absorb a terminal snapshot, returning true if the build is over.
   *
   * `ready` is terminal for the obvious reason; `failed` is terminal because the
   * server will not retry on its own — the user has to clear it first.
   */
  const settle = useCallback(
    (region: RegionState): boolean => {
      if (region.state === "ready") {
        remember(region);
        onReadyRef.current?.(region);
        return true;
      }
      if (region.state === "failed") {
        setError(region.error || region.message || "The build failed.");
        return true;
      }
      return false;
    },
    [remember],
  );

  /** Sleep, but through the one timer handle that cancel() can clear. */
  const sleep = useCallback((ms: number) => {
    return new Promise<void>((resolve) => {
      timer.current = setTimeout(() => {
        timer.current = null;
        resolve();
      }, ms);
    });
  }, []);

  const poll = useCallback(
    async (regionKey: string, gen: number, startedAt: number, immediate: boolean) => {
      let consecutiveErrors = 0;
      let first = true;
      while (gen === generation.current) {
        const elapsed = Date.now() - startedAt;
        if (elapsed > MAX_POLL_MS) {
          setState("failed");
          setError("The build is taking longer than expected. Try again in a moment.");
          return;
        }
        if (!(first && immediate)) {
          await sleep(pollDelayMs(elapsed));
          if (gen !== generation.current) return;
        }
        first = false;

        try {
          const region = await getRegion(regionKey);
          if (gen !== generation.current) return;
          consecutiveErrors = 0;
          apply(region);
          if (settle(region)) return;
        } catch (caught) {
          if (gen !== generation.current) return;
          // One dropped poll should not throw away a build that is still
          // running server-side; several in a row means we have lost it.
          consecutiveErrors += 1;
          if (consecutiveErrors >= MAX_POLL_ERRORS) {
            setState("failed");
            setError(describe(caught, "Lost contact with the build."));
            return;
          }
        }
      }
    },
    [apply, settle, sleep],
  );

  const start = useCallback(
    async (next: RegionTarget) => {
      generation.current += 1;
      const gen = generation.current;
      stopTimer();
      target.current = next;
      setError(null);
      setProgress(0);
      const initialLabel = next.label ?? next.place ?? null;
      setLabel(initialLabel);
      labelRef.current = initialLabel;

      const canRequest = Boolean(next.place) || (next.lat !== undefined && next.lon !== undefined);
      if (!canRequest) {
        // Watch-only: somebody else already started this build.
        if (!next.key) {
          setState("failed");
          setError("Nothing to build — no place or key was given.");
          return;
        }
        setKey(next.key);
        keyRef.current = next.key;
        setState("building");
        setStage("queued");
        setMessage("Checking on this area…");
        await poll(next.key, gen, Date.now(), true);
        return;
      }

      setState("requesting");
      setStage("queued");
      setMessage("Asking for this area…");
      try {
        const region = await requestRegion(
          next.place ? { place: next.place } : { lat: next.lat, lon: next.lon },
        );
        if (gen !== generation.current) return;
        apply(region);
        if (settle(region)) return;
        await poll(region.key, gen, Date.now(), false);
      } catch (caught) {
        if (gen !== generation.current) return;
        setState("failed");
        setError(describe(caught, "Could not start the build."));
      }
    },
    [apply, poll, settle, stopTimer],
  );

  const request = useCallback(
    (next: RegionTarget | string) => {
      void start(typeof next === "string" ? { place: next } : next);
    },
    [start],
  );

  /**
   * Clear a failed build, then ask for it again.
   *
   * The server keeps a failure recorded so it does not silently retry an area
   * Overture cannot support; asking again therefore has to delete that record
   * first, or the second request returns the same failure immediately.
   */
  const retry = useCallback(
    (override?: RegionTarget) => {
      const chosen = override ?? target.current;
      if (!chosen) return;
      const failedKey = chosen.key ?? keyRef.current;
      // A build that came to us as a bare key — from `region_building` or
      // `region_build_failed`, which name the key and nothing else — has nothing
      // to POST once the record is deleted. The server's own label for the region
      // is a place name ("Peoria, IL"), so it is the best re-request we have.
      const canRequest =
        Boolean(chosen.place) || (chosen.lat !== undefined && chosen.lon !== undefined);
      const next: RegionTarget =
        canRequest || !labelRef.current ? chosen : { ...chosen, place: labelRef.current };
      // Bump the generation now so an in-flight poll cannot write over the
      // "clearing" message while the DELETE is on the wire.
      generation.current += 1;
      stopTimer();
      setState("requesting");
      setError(null);
      setProgress(0);
      setMessage("Clearing the failed build…");
      void (async () => {
        if (failedKey) {
          // A failed DELETE is not worth surfacing: the retry either works
          // anyway or fails again with a message about the build itself.
          await clearRegion(failedKey).catch(() => undefined);
        }
        await start(next);
      })();
    },
    [start, stopTimer],
  );

  return {
    state,
    progress,
    stage,
    message,
    error,
    key,
    label,
    known,
    request,
    retry,
    cancel,
  };
}
