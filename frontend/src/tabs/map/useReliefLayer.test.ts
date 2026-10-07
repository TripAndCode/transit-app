import { renderHook } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { makeMockMap, type MockMap } from "../../test/mockMap";
import { RELIEF_LAYER, RELIEF_SOURCE, reliefHeight, type ReliefPoint } from "./reliefLayer";
import { useReliefLayer } from "./useReliefLayer";
import { LIVE_TRIPS_CLUSTER_LAYER, LIVE_TRIPS_LABEL_LAYER, LIVE_TRIPS_LAYER } from "./useOperationsMapLayers";
import { TIMELINE_LAYER } from "./useTimelineLayers";

const POINTS: ReliefPoint[] = [{ stop_id: "S1", lon: 132.4, lat: 34.4, delay_min: 2 }];
const LATER: ReliefPoint[] = [{ ...POINTS[0], delay_min: 6 }];
const LIVE = [LIVE_TRIPS_CLUSTER_LAYER, LIVE_TRIPS_LAYER, LIVE_TRIPS_LABEL_LAYER];

type Source = { data: GeoJSON.FeatureCollection; setData: (d: unknown) => void };

function liveMap(): MockMap {
  return makeMockMap([
    { id: "basemap", type: "raster" },
    { id: LIVE_TRIPS_CLUSTER_LAYER, type: "circle" },
    { id: LIVE_TRIPS_LAYER, type: "circle" },
    { id: LIVE_TRIPS_LABEL_LAYER, type: "symbol" },
  ]);
}
function mount(map: MockMap, on: boolean, points = POINTS, crossFade = 600) {
  return renderHook(({ o, p, c }) => { const ref = useRef(map as never); useReliefLayer(ref, 0, o, p, c); },
    { initialProps: { o: on, p: points, c: crossFade } });
}
function source(map: MockMap): Source {
  return map.getSource(RELIEF_SOURCE) as Source;
}
function heightOf(data: unknown): number {
  return (data as GeoJSON.FeatureCollection).features[0].properties!.h as number;
}

/** A hand-cranked animation clock: `frame(ms)` runs the queued callback at
 *  that timestamp, so a tween is stepped deterministically. */
let queued: FrameRequestCallback | null = null;
const cancelled: number[] = [];
function frame(ms: number) {
  const cb = queued;
  queued = null;
  cb?.(ms);
}

beforeEach(() => {
  queued = null;
  cancelled.length = 0;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { queued = cb; return 7; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => { cancelled.push(id); queued = null; });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useReliefLayer", () => {
  it("adds nothing while off", () => {
    const map = liveMap();
    mount(map, false);
    expect(map.getLayer(RELIEF_LAYER)).toBeUndefined();
    expect(map.getSource(RELIEF_SOURCE)).toBeUndefined();
  });

  it("adds the extrusion layer beneath every live layer, so the vehicle dots stay on top and clickable", () => {
    const map = liveMap();
    mount(map, true);
    const ids = map.layers.map((l) => l.id);
    for (const id of LIVE) expect(ids.indexOf(RELIEF_LAYER)).toBeLessThan(ids.indexOf(id));
    expect(map.getLayer(RELIEF_LAYER)!.type).toBe("fill-extrusion");
    expect(source(map).data.features).toHaveLength(1);
  });

  it("never hides the live or playback layers it stands among", () => {
    const map = makeMockMap([{ id: "basemap", type: "raster" }, ...LIVE.map((id) => ({ id })), { id: TIMELINE_LAYER }]);
    const { rerender } = mount(map, true);
    rerender({ o: true, p: LATER, c: 0 });
    rerender({ o: false, p: LATER, c: 0 });
    expect(Object.keys(map.layout)).toEqual([]);
  });

  it("removes its layer and source when switched off", () => {
    const map = liveMap();
    const { rerender } = mount(map, true);
    rerender({ o: false, p: POINTS, c: 600 });
    expect(map.getLayer(RELIEF_LAYER)).toBeUndefined();
    expect(map.getSource(RELIEF_SOURCE)).toBeUndefined();
  });

  it("under reduced motion writes a new reading exactly once, with no tween and the paint re-written", () => {
    const map = liveMap();
    const { rerender } = mount(map, true, POINTS, 0);
    const setData = vi.spyOn(source(map), "setData");
    rerender({ o: true, p: LATER, c: 0 });
    expect(setData).toHaveBeenCalledTimes(1);
    expect(heightOf(setData.mock.calls[0][0])).toBe(5.5 * 55 + 6);
    expect(queued).toBeNull();
    expect(map.layers.filter((l) => l.id === RELIEF_LAYER)).toHaveLength(1);
  });

  it("tweens a changed reading over the cross-fade in at most 16 throttled steps, landing exactly on the new height", () => {
    const map = liveMap();
    const { rerender } = mount(map, true);
    const setData = vi.spyOn(source(map), "setData");
    rerender({ o: true, p: LATER, c: 600 });
    expect(setData).not.toHaveBeenCalled();
    for (let ms = 0; ms <= 700; ms += 4) frame(ms);
    expect(queued).toBeNull();
    const heights = setData.mock.calls.map(([d]) => heightOf(d));
    expect(heights.length).toBeGreaterThan(2);
    expect(heights.length).toBeLessThanOrEqual(16);
    for (let i = 1; i < heights.length; i += 1) expect(heights[i]).toBeGreaterThan(heights[i - 1]);
    expect(heights[0]).toBeGreaterThan(reliefHeight(2));
    expect(heights.at(-1)).toBe(reliefHeight(6));
  });

  it("moves the columns on the first frame of every tween, so a reading replaced each frame (a scrub) still follows", () => {
    const map = liveMap();
    const { rerender } = mount(map, true);
    const setData = vi.spyOn(source(map), "setData");
    for (let i = 0; i < 5; i += 1) {
      rerender({ o: true, p: [{ ...POINTS[0], delay_min: 3 + i }], c: 600 });
      frame(i * 20);
    }
    const heights = setData.mock.calls.map(([d]) => heightOf(d));
    expect(heights).toHaveLength(5);
    for (let i = 1; i < heights.length; i += 1) expect(heights[i]).toBeGreaterThan(heights[i - 1]);
  });

  it("writes a new reading on the first frame even while another source's reload holds the style unloaded", () => {
    const map = liveMap();
    map.reloadsOnSetData = true;
    const { rerender } = mount(map, true);
    // The playback dots' own write in the same commit leaves the style unloaded.
    map.addSource("other", { type: "geojson" });
    (map.getSource("other") as Source).setData({});
    expect(map.isStyleLoaded()).toBe(false);
    const setData = vi.spyOn(source(map), "setData");
    rerender({ o: true, p: LATER, c: 600 });
    frame(0);
    expect(setData).toHaveBeenCalledTimes(1);
  });

  it("goes beneath the playback dots too when no live layer is on the map yet", () => {
    const map = makeMockMap([{ id: "basemap", type: "raster" }, { id: TIMELINE_LAYER, type: "circle" }]);
    mount(map, true);
    const ids = map.layers.map((l) => l.id);
    expect(ids.indexOf(RELIEF_LAYER)).toBeLessThan(ids.indexOf(TIMELINE_LAYER));
  });

  it("does not restart a running tween for a re-render carrying the same reading", () => {
    const map = liveMap();
    const { rerender } = mount(map, true);
    rerender({ o: true, p: LATER, c: 600 });
    frame(0);
    rerender({ o: true, p: [...LATER], c: 600 });
    expect(cancelled).toEqual([]);
    expect(queued).not.toBeNull();
  });

  it("cancels a running tween when a newer reading arrives, and on unmount", () => {
    const map = liveMap();
    const { rerender, unmount } = mount(map, true);
    rerender({ o: true, p: LATER, c: 600 });
    frame(0);
    rerender({ o: true, p: POINTS, c: 600 });
    expect(cancelled).toHaveLength(1);
    unmount();
    expect(cancelled).toHaveLength(2);
    expect(queued).toBeNull();
  });

  it("waits for the style to settle before touching the map", () => {
    const map = makeMockMap([{ id: "basemap", type: "raster" }], false);
    mount(map, true);
    expect(map.getLayer(RELIEF_LAYER)).toBeUndefined();
    map.settleStyle();
    expect(map.getLayer(RELIEF_LAYER)).toBeDefined();
  });
});
