import { renderHook } from "@testing-library/react";
import { useRef } from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { makeMockMap, type MockMap } from "../../test/mockMap";
import { PEARL_LAYER, PEARL_SOURCE, pearlGradient } from "./pearl";
import { usePearlLayer } from "./usePearlLayer";
import { stubReducedMotion } from "../../test/reducedMotion";
import type { LiveTripProgressResponse } from "../../api/types";

afterEach(() => vi.restoreAllMocks());
const stop = (seq: number, lon: number, lat: number) => ({ stop_sequence: seq, stop_id: null, stop_name: null, stop_lat: lat, stop_lon: lon, scheduled_time: null, dep_delay: 0, reported_at: "2026-10-03T00:00:00Z" });
const PROGRESS = { trip_id: "t", route_code: "3", headsign: null, direction_id: null, latest_captured_at: null, stops: [stop(1, 0, 0), stop(2, 1, 1)] } as LiveTripProgressResponse;

function mount(map: MockMap, on: boolean, progress: LiveTripProgressResponse | undefined = PROGRESS) {
  return renderHook(({ o, p }) => { const ref = useRef(map as never); usePearlLayer(ref, 0, p, o); }, { initialProps: { o: on, p: progress as LiveTripProgressResponse | undefined } });
}

describe("usePearlLayer", () => {
  it("adds a lineMetrics source and a gradient line layer for the reported segments", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => { frames.push(cb); return 1; });
    const map = makeMockMap();
    mount(map, true);
    expect((map.getSource(PEARL_SOURCE) as { lineMetrics: boolean }).lineMetrics).toBe(true);
    expect(map.getLayer(PEARL_LAYER)!.type).toBe("line");
    expect(frames).toHaveLength(1);
  });
  it("removes the layer when there is no line to light", () => {
    const map = makeMockMap();
    const { rerender } = mount(map, true);
    rerender({ o: true, p: undefined });
    expect(map.getLayer(PEARL_LAYER)).toBeUndefined();
    expect(map.getSource(PEARL_SOURCE)).toBeUndefined();
  });
  it("paints the hidden gradient and runs no loop when off", () => {
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const map = makeMockMap();
    mount(map, false);
    expect(map.getLayer(PEARL_LAYER)).toBeUndefined();
    expect(raf).not.toHaveBeenCalled();
  });
  it("under reduced motion keeps the layer but never advances it", () => {
    stubReducedMotion();
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const map = makeMockMap();
    mount(map, true);
    expect(map.getLayer(PEARL_LAYER)).toBeDefined();
    expect(map.getPaintProperty(PEARL_LAYER, "line-gradient") ?? (map.getLayer(PEARL_LAYER)!.paint as Record<string, unknown>)["line-gradient"]).toEqual(pearlGradient(-1));
    expect(raf).not.toHaveBeenCalled();
  });
});
