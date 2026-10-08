import { act, renderHook } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeMockMap, type MockLayer } from "../../test/mockMap";
import { severityStepColors, surfaceColorResolved } from "../../styles/tokens";
import { applyTheme } from "../../styles/theme";
import { CROSS_FADE_MS, GHOST_OPACITY } from "./playbackFrames";
import { LIVE_TRIPS_CLUSTER_LAYER, LIVE_TRIPS_LABEL_LAYER, LIVE_TRIPS_LAYER } from "./useOperationsMapLayers";
import {
  TIMELINE_LAYER,
  TIMELINE_SOURCE,
  timelineCirclePaint,
  useTimelineLayers,
} from "./useTimelineLayers";
import type { TimelineFrame } from "../../api/types";

/** What MapLibre hands a camera-event listener when a person moved the map. */
const GESTURE = { originalEvent: new MouseEvent("mousedown") };

const FRAMES: TimelineFrame[] = [
  {
    t: "05:00",
    points: [{ stop_id: "S1", stop_name: "A", lon: 140.7, lat: 40.8, avg_delay_min: 1, samples: 5 }],
    mean_delay_min: 1,
    samples: 5,
  },
  {
    t: "06:00",
    points: [{ stop_id: "S2", stop_name: "B", lon: 140.8, lat: 40.9, avg_delay_min: 7, samples: 9 }],
    mean_delay_min: 7,
    samples: 9,
  },
];

function mount(map: ReturnType<typeof makeMockMap>, args: Parameters<typeof useTimelineLayers> extends [unknown, ...infer R] ? R : never) {
  return renderHook(() => {
    const mapRef = useRef(map as never);
    useTimelineLayers(mapRef, ...args);
  });
}

describe("timelineCirclePaint", () => {
  it("weights the two ghost frames below the frame being played", () => {
    const paint = timelineCirclePaint(CROSS_FADE_MS);
    expect(paint["circle-opacity"]).toEqual([
      "match", ["get", "age"], 0, GHOST_OPACITY[0], 1, GHOST_OPACITY[1], 2, GHOST_OPACITY[2], 0,
    ]);
  });

  it("cross-fades the opacity over the shared duration", () => {
    expect(timelineCirclePaint(CROSS_FADE_MS)["circle-opacity-transition"]).toEqual({
      duration: CROSS_FADE_MS,
      delay: 0,
    });
  });

  it("drops the fade to nothing when the caller asks for stepping only", () => {
    expect(timelineCirclePaint(0)["circle-opacity-transition"]).toEqual({ duration: 0, delay: 0 });
  });

  it("colours by the delay ramp and rings only the frame being played", () => {
    const paint = timelineCirclePaint(CROSS_FADE_MS);
    expect(paint["circle-color"]).toEqual(["step", ["get", "delay_min"], ...severityStepColors()]);
    expect(paint["circle-stroke-color"]).toBe(surfaceColorResolved());
    expect(paint["circle-stroke-width"]).toEqual(["match", ["get", "age"], 0, 1.2, 0]);
  });
});

describe("useTimelineLayers", () => {
  it("does nothing at all while playback mode is off", () => {
    const map = makeMockMap();
    mount(map, [0, FRAMES, 0, false, false, vi.fn()]);
    expect(map.getSource(TIMELINE_SOURCE)).toBeUndefined();
    expect(map.getLayer(TIMELINE_LAYER)).toBeUndefined();
  });

  it("adds its own source and feeds it the current frame plus its ghosts", () => {
    const map = makeMockMap();
    mount(map, [0, FRAMES, 1, true, false, vi.fn()]);
    const source = map.getSource(TIMELINE_SOURCE) as { data: GeoJSON.FeatureCollection };
    expect(source.data.features.map((f) => f.properties!.age)).toEqual([1, 0]);
    expect(map.getLayer(TIMELINE_LAYER)).toBeTruthy();
  });

  it("feeds the next frame at once even while another source's reload holds the style unloaded", () => {
    const map = makeMockMap();
    map.reloadsOnSetData = true;
    const { rerender } = renderHook(({ index }) => {
      const mapRef = useRef(map as never);
      useTimelineLayers(mapRef, 0, FRAMES, index, true, false, vi.fn());
    }, { initialProps: { index: 0 } });
    map.addSource("relief", { type: "geojson" });
    (map.getSource("relief") as { setData: (d: unknown) => void }).setData({});
    expect(map.isStyleLoaded()).toBe(false);
    rerender({ index: 1 });
    const source = map.getSource(TIMELINE_SOURCE) as { data: GeoJSON.FeatureCollection };
    expect(source.data.features.map((f) => f.properties!.age)).toEqual([1, 0]);
  });

  it("hides the live layers while playing and shows them again on exit", () => {
    const map = makeMockMap([
      { id: LIVE_TRIPS_LAYER },
      { id: LIVE_TRIPS_LABEL_LAYER },
      { id: LIVE_TRIPS_CLUSTER_LAYER },
    ]);
    const { rerender, unmount } = renderHook(
      ({ active }: { active: boolean }) => {
        const mapRef = useRef(map as never);
        useTimelineLayers(mapRef, 0, FRAMES, 0, active, false, vi.fn());
      },
      { initialProps: { active: true } },
    );
    expect(map.layout[`${LIVE_TRIPS_LAYER}|visibility`]).toBe("none");
    expect(map.layout[`${LIVE_TRIPS_CLUSTER_LAYER}|visibility`]).toBe("none");

    rerender({ active: false });
    expect(map.layout[`${LIVE_TRIPS_LAYER}|visibility`]).toBe("visible");
    expect(map.getSource(TIMELINE_SOURCE)).toBeUndefined();
    unmount();
  });

  it("pauses on any camera gesture, so a reader who grabs the map is not fought", () => {
    const map = makeMockMap();
    const onInteract = vi.fn();
    mount(map, [0, FRAMES, 0, true, false, onInteract]);
    map.fire("dragstart", GESTURE);
    map.fire("zoomstart", GESTURE);
    expect(onInteract).toHaveBeenCalledTimes(2);
  });

  it("keeps playing through a camera move the app made itself, such as the relief tilt", () => {
    const map = makeMockMap();
    const onInteract = vi.fn();
    mount(map, [0, FRAMES, 0, true, false, onInteract]);
    map.fire("pitchstart", {});
    map.fire("zoomstart");
    expect(onInteract).not.toHaveBeenCalled();
    map.fire("pitchstart", GESTURE);
    expect(onInteract).toHaveBeenCalledTimes(1);
  });

  it("stops listening for gestures once playback mode is left", () => {
    const map = makeMockMap();
    const onInteract = vi.fn();
    const { rerender } = renderHook(
      ({ active }: { active: boolean }) => {
        const mapRef = useRef(map as never);
        useTimelineLayers(mapRef, 0, FRAMES, 0, active, false, onInteract);
      },
      { initialProps: { active: true } },
    );
    rerender({ active: false });
    map.fire("dragstart", GESTURE);
    expect(onInteract).not.toHaveBeenCalled();
  });
});

describe("useTimelineLayers repaints an existing playback layer", () => {
  const LIGHT = { "--bg-surface": "#ffffff", "--delay-severe": "#A8391F" };
  const DARK = { "--bg-surface": "#141726", "--delay-severe": "#F0837A" };

  function paintTokens(tokens: typeof LIGHT) {
    for (const [prop, value] of Object.entries(tokens)) document.documentElement.style.setProperty(prop, value);
  }

  afterEach(() => {
    for (const prop of Object.keys(LIGHT)) document.documentElement.style.removeProperty(prop);
    delete document.documentElement.dataset.theme;
  });

  it("re-resolves every theme colour when the theme toggles mid-playback", () => {
    document.documentElement.dataset.theme = "light";
    paintTokens(LIGHT);
    const map = makeMockMap();
    mount(map, [0, FRAMES, 0, true, false, vi.fn()]);

    paintTokens(DARK);
    act(() => applyTheme("dark"));

    for (const [property, value] of Object.entries(timelineCirclePaint(CROSS_FADE_MS))) {
      expect(map.getPaintProperty(TIMELINE_LAYER, property), property).toEqual(value);
    }
    expect(map.getPaintProperty(TIMELINE_LAYER, "circle-stroke-color")).toBe(DARK["--bg-surface"]);
    expect(map.getPaintProperty(TIMELINE_LAYER, "circle-color")).toContain(DARK["--delay-severe"]);
  });

  it("drops the cross-fade once playback falls back to stepping", () => {
    const map = makeMockMap();
    const { rerender } = renderHook(
      ({ steppingOnly }: { steppingOnly: boolean }) => {
        const mapRef = useRef(map as never);
        useTimelineLayers(mapRef, 0, FRAMES, 0, true, steppingOnly, vi.fn());
      },
      { initialProps: { steppingOnly: false } },
    );
    expect((map.getLayer(TIMELINE_LAYER) as MockLayer).paint?.["circle-opacity-transition"])
      .toEqual({ duration: CROSS_FADE_MS, delay: 0 });

    rerender({ steppingOnly: true });
    expect(map.getPaintProperty(TIMELINE_LAYER, "circle-opacity-transition")).toEqual({ duration: 0, delay: 0 });
  });
});
