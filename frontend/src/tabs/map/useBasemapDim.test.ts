import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { useRef } from "react";
import { makeMockMap, type MockMap } from "../../test/mockMap";
import { useBasemapDim, SCRIM_LAYER } from "./useBasemapDim";

function run(map: MockMap, epoch = 0) {
  return renderHook(() => {
    const mapRef = useRef(map as never);
    useBasemapDim(mapRef, epoch);
  });
}

describe("useBasemapDim", () => {
  it("sets zoom-gated raster mute on the basemap layer", () => {
    const map = makeMockMap();
    run(map);
    expect(map.getPaintProperty("basemap", "raster-saturation")).toEqual([
      "interpolate", ["linear"], ["zoom"], 12, 0, 14, -0.5,
    ]);
    expect(map.getPaintProperty("basemap", "raster-contrast")).toEqual([
      "interpolate", ["linear"], ["zoom"], 12, 0, 14, -0.12,
    ]);
    expect(map.getPaintProperty("basemap", "raster-brightness-max")).toEqual([
      "interpolate", ["linear"], ["zoom"], 12, 1, 14, 0.92,
    ]);
  });

  it("inserts a zoom-gated white scrim directly above basemap", () => {
    const map = makeMockMap();
    run(map);
    const ids = map.layers.map((l) => l.id);
    expect(ids).toEqual(["basemap", SCRIM_LAYER]);
    const scrim = map.getLayer(SCRIM_LAYER)!;
    expect(scrim.type).toBe("background");
    expect((scrim.paint as Record<string, unknown>)["background-opacity"]).toEqual([
      "interpolate", ["linear"], ["zoom"], 12, 0, 14, 0.2,
    ]);
  });

  it("is idempotent — re-render does not add a second scrim", () => {
    const map = makeMockMap();
    const { rerender } = run(map);
    rerender();
    expect(map.layers.filter((l) => l.id === SCRIM_LAYER).length).toBe(1);
  });

  it("defers apply until the style settles when not yet loaded", () => {
    // style not loaded yet → hook re-arms on styledata, applies nothing now
    const map = makeMockMap([{ id: "basemap", type: "raster" }], false);
    run(map);
    expect(map.getLayer(SCRIM_LAYER)).toBeUndefined(); // nothing applied yet
    map.settleStyle(); // isStyleLoaded() → true + emits styledata
    expect(map.getLayer(SCRIM_LAYER)).toBeDefined(); // applied once ready
    expect(map.getPaintProperty("basemap", "raster-saturation")).toEqual([
      "interpolate", ["linear"], ["zoom"], 12, 0, 14, -0.5,
    ]);
  });

  it("re-attaches even when a styledata fires before the style is ready", () => {
    // The race that broke the locale switch: a styledata arrives while
    // isStyleLoaded() is still false (tiles loading). whenStyleReady must keep
    // listening and apply on the LATER styledata once the style is ready,
    // rather than giving up like the old one-shot once("style.load").
    const map = makeMockMap([{ id: "basemap", type: "raster" }], false);
    run(map);
    map.fire("styledata"); // early event, style not ready yet → must NOT apply
    expect(map.getLayer(SCRIM_LAYER)).toBeUndefined();
    map.settleStyle(); // now ready → applies
    expect(map.getLayer(SCRIM_LAYER)).toBeDefined();
  });

  it("re-attaches via the idle backstop when no qualifying styledata fires", () => {
    // The real raster-tile failure: isStyleLoaded() flips true but only `idle`
    // signals it (tile loads emit sourcedata, not styledata). The scrim must
    // still attach off `idle`, not hang waiting for a styledata that won't come.
    const map = makeMockMap([{ id: "basemap", type: "raster" }], false);
    run(map);
    expect(map.getLayer(SCRIM_LAYER)).toBeUndefined();
    map.settleViaIdle(); // only `idle` fires
    expect(map.getLayer(SCRIM_LAYER)).toBeDefined();
  });

  it("widens the zoom-gated ramp to start at 6 (not 12) when isRouteMode is true", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useBasemapDim(mapRef, 0, true);
    });
    expect(map.getPaintProperty("basemap", "raster-saturation")).toEqual([
      "interpolate", ["linear"], ["zoom"], 6, 0, 14, -0.5,
    ]);
    expect(map.getPaintProperty("basemap", "raster-contrast")).toEqual([
      "interpolate", ["linear"], ["zoom"], 6, 0, 14, -0.12,
    ]);
    expect(map.getPaintProperty("basemap", "raster-brightness-max")).toEqual([
      "interpolate", ["linear"], ["zoom"], 6, 1, 14, 0.92,
    ]);
    const scrim = map.getLayer(SCRIM_LAYER)!;
    expect((scrim.paint as Record<string, unknown>)["background-opacity"]).toEqual([
      "interpolate", ["linear"], ["zoom"], 6, 0, 14, 0.2,
    ]);
  });

  it("keeps the 12->14 ramp when isRouteMode is false or omitted (default heatmap view)", () => {
    const map = makeMockMap();
    renderHook(() => {
      const mapRef = useRef(map as never);
      useBasemapDim(mapRef, 0, false);
    });
    expect(map.getPaintProperty("basemap", "raster-saturation")).toEqual([
      "interpolate", ["linear"], ["zoom"], 12, 0, 14, -0.5,
    ]);
  });
});
