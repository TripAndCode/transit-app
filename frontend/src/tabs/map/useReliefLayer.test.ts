import { renderHook } from "@testing-library/react";
import { useRef } from "react";
import { describe, it, expect } from "vitest";
import { makeMockMap, type MockMap } from "../../test/mockMap";
import { RELIEF_LAYER, RELIEF_SOURCE, type ReliefPoint } from "./reliefLayer";
import { useReliefLayer } from "./useReliefLayer";
import { LIVE_TRIPS_CLUSTER_COUNT_LAYER, LIVE_TRIPS_CLUSTER_LAYER, LIVE_TRIPS_LABEL_LAYER, LIVE_TRIPS_LAYER } from "./useOperationsMapLayers";
import { TIMELINE_LAYER } from "./useTimelineLayers";

const POINTS: ReliefPoint[] = [{ stop_id: "S1", lon: 132.4, lat: 34.4, delay_min: 2 }];
const LIVE = [LIVE_TRIPS_CLUSTER_LAYER, LIVE_TRIPS_CLUSTER_COUNT_LAYER, LIVE_TRIPS_LAYER];

function liveMap(): MockMap {
  return makeMockMap([{ id: "basemap", type: "raster" }, ...LIVE.map((id) => ({ id, type: "circle" })), { id: LIVE_TRIPS_LABEL_LAYER, type: "symbol" }]);
}
function mount(map: MockMap, on: boolean, points = POINTS, crossFade = 600, playbackOn = false) {
  return renderHook(({ o, p, c, pb }) => { const ref = useRef(map as never); useReliefLayer(ref, 0, o, p, c, pb); },
    { initialProps: { o: on, p: points, c: crossFade, pb: playbackOn } });
}

describe("useReliefLayer", () => {
  it("adds nothing while off and leaves the live layers visible", () => {
    const map = liveMap();
    mount(map, false);
    expect(map.getLayer(RELIEF_LAYER)).toBeUndefined();
    for (const id of LIVE) expect(map.getLayoutProperty(id, "visibility")).toBe("visible");
  });
  it("adds the extrusion layer below the labels and hides the circle layers while on", () => {
    const map = liveMap();
    mount(map, true);
    const ids = map.layers.map((l) => l.id);
    expect(ids.indexOf(RELIEF_LAYER)).toBeLessThan(ids.indexOf(LIVE_TRIPS_LABEL_LAYER));
    expect(map.getLayer(RELIEF_LAYER)!.type).toBe("fill-extrusion");
    expect((map.getSource(RELIEF_SOURCE) as { data: GeoJSON.FeatureCollection }).data.features).toHaveLength(1);
    for (const id of LIVE) expect(map.getLayoutProperty(id, "visibility")).toBe("none");
    expect(map.getLayoutProperty(LIVE_TRIPS_LABEL_LAYER, "visibility")).toBeUndefined();
  });
  it("feeds new points through setData and re-writes the paint, rather than re-adding", () => {
    const map = liveMap();
    const { rerender } = mount(map, true);
    rerender({ o: true, p: [{ ...POINTS[0], delay_min: 6 }], c: 0, pb: false });
    expect(map.layers.filter((l) => l.id === RELIEF_LAYER)).toHaveLength(1);
    expect((map.getSource(RELIEF_SOURCE) as { data: GeoJSON.FeatureCollection }).data.features[0].properties!.h).toBe(5.5 * 55 + 6);
    expect(map.getPaintProperty(RELIEF_LAYER, "fill-extrusion-height-transition")).toEqual({ duration: 0, delay: 0 });
  });
  it("restores the circle layers when switched off, unless playback owns them", () => {
    const map = liveMap();
    const { rerender } = mount(map, true);
    rerender({ o: false, p: POINTS, c: 600, pb: false });
    expect(map.getLayer(RELIEF_LAYER)).toBeUndefined();
    for (const id of LIVE) expect(map.getLayoutProperty(id, "visibility")).toBe("visible");
    rerender({ o: true, p: POINTS, c: 600, pb: true });
    rerender({ o: false, p: POINTS, c: 600, pb: true });
    for (const id of LIVE) expect(map.getLayoutProperty(id, "visibility")).toBe("none");
  });
  it("hides the playback circle layer too while on, and restores it when off", () => {
    const map = makeMockMap([{ id: "basemap", type: "raster" }, { id: TIMELINE_LAYER, type: "circle" }]);
    const { rerender } = mount(map, true, POINTS, 600, true);
    expect(map.getLayoutProperty(TIMELINE_LAYER, "visibility")).toBe("none");
    rerender({ o: false, p: POINTS, c: 600, pb: true });
    expect(map.getLayoutProperty(TIMELINE_LAYER, "visibility")).toBe("visible");
  });
  it("waits for the style to settle before touching the map", () => {
    const map = makeMockMap([{ id: "basemap", type: "raster" }], false);
    mount(map, true);
    expect(map.getLayer(RELIEF_LAYER)).toBeUndefined();
    map.settleStyle();
    expect(map.getLayer(RELIEF_LAYER)).toBeDefined();
  });
});
