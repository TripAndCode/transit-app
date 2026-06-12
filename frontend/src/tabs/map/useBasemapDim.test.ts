import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import { useRef } from "react";
import { makeMockMap, type MockMap } from "../../test/mockMap";
import { useBasemapDim, SCRIM_LAYER } from "./useBasemapDim";

function run(map: MockMap, styleLoaded = true, epoch = 0) {
  return renderHook(() => {
    const mapRef = useRef(map as never);
    const styleLoadedRef = useRef(styleLoaded);
    useBasemapDim(mapRef, styleLoadedRef, epoch);
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

  it("defers apply until style.load when the style is not yet loaded", () => {
    // style not loaded yet → hook must register once("style.load"), not apply now
    const map = makeMockMap([{ id: "basemap", type: "raster" }], false);
    run(map);
    expect(map.getLayer(SCRIM_LAYER)).toBeUndefined(); // nothing applied yet
    map.fireOnce("style.load"); // flips isStyleLoaded() true + runs the handler
    expect(map.getLayer(SCRIM_LAYER)).toBeDefined(); // applied after the event
    expect(map.getPaintProperty("basemap", "raster-saturation")).toEqual([
      "interpolate", ["linear"], ["zoom"], 12, 0, 14, -0.5,
    ]);
  });
});
