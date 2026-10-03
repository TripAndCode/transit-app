import { afterEach, describe, expect, it, vi } from "vitest";
import type { Map as MLMap } from "maplibre-gl";
import {
  MOTION,
  easeOutCamera,
  fitAll,
  focusRoute,
  inspectTrip,
  revealAgency,
} from "./cameraChoreography";

const BOUNDS: [[number, number], [number, number]] = [[140.6, 40.7], [140.9, 40.9]];

function fakeMap(camera?: { center: [number, number]; zoom: number }) {
  return {
    jumpTo: vi.fn(),
    flyTo: vi.fn(),
    easeTo: vi.fn(),
    fitBounds: vi.fn(),
    getZoom: vi.fn(() => 11),
    cameraForBounds: vi.fn(() => camera),
  };
}

type FakeMap = ReturnType<typeof fakeMap>;

function asMap(map: FakeMap): MLMap {
  return map as unknown as MLMap;
}

function setReducedMotion(reduce: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: reduce && query.includes("prefers-reduced-motion"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

/** Every call the four choreography functions can land on the camera. */
function cameraCalls(map: FakeMap) {
  return [
    ...map.jumpTo.mock.calls,
    ...map.flyTo.mock.calls,
    ...map.easeTo.mock.calls,
    ...map.fitBounds.mock.calls.map((call) => [call[1]]),
  ].map(([options]) => options as Record<string, unknown>);
}

afterEach(() => {
  setReducedMotion(false);
});

describe("easeOutCamera", () => {
  it("runs from 0 to 1 and decelerates hard, matching --ease-out", () => {
    expect(easeOutCamera(0)).toBe(0);
    expect(easeOutCamera(1)).toBe(1);
    // cubic-bezier(.22, 1, .36, 1) is past 90% of the distance at the
    // halfway point -- that front-loading is what makes it read as "arrives
    // and settles" rather than "travels at a constant speed".
    expect(easeOutCamera(0.5)).toBeGreaterThan(0.9);
    expect(easeOutCamera(0.5)).toBeLessThan(1);
  });

  it("is monotonic across the whole interval", () => {
    let previous = -1;
    for (let step = 0; step <= 20; step += 1) {
      const value = easeOutCamera(step / 20);
      expect(value).toBeGreaterThanOrEqual(previous);
      previous = value;
    }
  });
});

describe("revealAgency", () => {
  it("pitches in from a wider start and flies for the reveal duration", () => {
    const map = fakeMap({ center: [140.75, 40.82], zoom: 12 });
    revealAgency(asMap(map), BOUNDS);

    expect(map.jumpTo).toHaveBeenCalledWith(
      expect.objectContaining({ center: [140.75, 40.82], zoom: 11.4, pitch: 30 }),
    );
    const [flyOptions] = map.flyTo.mock.calls[0];
    expect(flyOptions).toMatchObject({ center: [140.75, 40.82], zoom: 12, pitch: 0, duration: MOTION.reveal });
    expect(flyOptions.easing).toBe(easeOutCamera);
  });

  it("falls back to a framed fit when the camera cannot be derived", () => {
    const map = fakeMap(undefined);
    revealAgency(asMap(map), BOUNDS);

    expect(map.flyTo).not.toHaveBeenCalled();
    expect(map.jumpTo).not.toHaveBeenCalled();
    expect(map.fitBounds).toHaveBeenCalledWith(BOUNDS, expect.objectContaining({ duration: MOTION.reveal }));
  });

  it("lands flat and instantly under reduced motion", () => {
    setReducedMotion(true);
    const map = fakeMap({ center: [140.75, 40.82], zoom: 12 });
    revealAgency(asMap(map), BOUNDS);

    expect(map.flyTo).not.toHaveBeenCalled();
    expect(map.jumpTo).toHaveBeenCalledWith({ center: [140.75, 40.82], zoom: 12, pitch: 0 });
  });
});

describe("the flat moves", () => {
  it("frames a route and every trip over the move duration, never pitching", () => {
    const map = fakeMap();
    focusRoute(asMap(map), BOUNDS);
    fitAll(asMap(map), BOUNDS);

    for (const [, options] of map.fitBounds.mock.calls) {
      expect(options).toMatchObject({ duration: MOTION.move, easing: easeOutCamera });
      expect(options).not.toHaveProperty("pitch");
    }
  });

  it("eases to a trip without zooming back out, and never pitches", () => {
    const map = fakeMap();
    inspectTrip(asMap(map), [140.8, 40.85]);

    expect(map.easeTo).toHaveBeenCalledWith(
      expect.objectContaining({ center: [140.8, 40.85], zoom: 13, duration: MOTION.move }),
    );
    expect(map.easeTo.mock.calls[0][0]).not.toHaveProperty("pitch");
  });

  it("keeps the current zoom when it is already closer than the inspect floor", () => {
    const map = fakeMap();
    map.getZoom.mockReturnValue(15);
    inspectTrip(asMap(map), [140.8, 40.85]);

    expect(map.easeTo.mock.calls[0][0]).toMatchObject({ zoom: 15 });
  });

  it("accepts an explicit zoom, for stepping into a cluster", () => {
    const map = fakeMap();
    inspectTrip(asMap(map), [140.8, 40.85], 13.5);

    expect(map.easeTo.mock.calls[0][0]).toMatchObject({ zoom: 13.5 });
  });
});

describe("reduced motion", () => {
  it("drives every move to zero duration", () => {
    setReducedMotion(true);
    const map = fakeMap({ center: [140.75, 40.82], zoom: 12 });
    revealAgency(asMap(map), BOUNDS);
    focusRoute(asMap(map), BOUNDS);
    fitAll(asMap(map), BOUNDS);
    inspectTrip(asMap(map), [140.8, 40.85]);

    const calls = cameraCalls(map);
    expect(calls.length).toBeGreaterThan(0);
    for (const options of calls) {
      expect(options.duration ?? 0).toBe(0);
      expect(options.easing).toBeUndefined();
    }
  });

  it("is the only thing that removes the reveal's pitch", () => {
    const moving = fakeMap({ center: [140.75, 40.82], zoom: 12 });
    revealAgency(asMap(moving), BOUNDS);
    expect(cameraCalls(moving).some((options) => options.pitch === 30)).toBe(true);

    setReducedMotion(true);
    const still = fakeMap({ center: [140.75, 40.82], zoom: 12 });
    revealAgency(asMap(still), BOUNDS);
    expect(cameraCalls(still).every((options) => (options.pitch ?? 0) === 0)).toBe(true);
  });
});
