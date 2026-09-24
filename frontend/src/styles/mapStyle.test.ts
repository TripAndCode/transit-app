import { describe, it, expect, beforeEach } from "vitest";
import {
  buildStyle,
  buildThumbnailUrl,
  DEFAULT_THUMBNAIL_VIEW,
  MAX_DIM_AMOUNT,
  MAP_STYLES,
  DEFAULT_MAP_STYLE_ID,
  readMapStylePref,
  writeMapStylePref,
  readMapDimPref,
  writeMapDimPref,
} from "./mapStyle";

type Src = Record<string, { tiles?: string[]; attribution?: string }>;
const basemap = (id: Parameters<typeof buildStyle>[0], lang = "ja") =>
  (buildStyle(id, lang).sources as Src).basemap;

describe("buildStyle", () => {
  it("returns a v8 raster style for pale with the GSI pale tiles + attribution", () => {
    const s = buildStyle("pale", "ja");
    expect(s.version).toBe(8);
    expect(basemap("pale").tiles?.[0]).toContain("/xyz/pale/");
    expect(basemap("pale").attribution).toContain("国土地理院");
    expect(s.layers[0]).toMatchObject({ type: "raster", source: "basemap" });
  });

  it("returns the OSM tiles + attribution for the osm style", () => {
    expect(basemap("osm").tiles?.[0]).toContain("tile.openstreetmap.org");
    expect(basemap("osm").tiles?.length).toBe(3); // a/b/c subdomains
    expect(basemap("osm").attribution).toContain("OpenStreetMap");
  });

  it("swaps std to the english tiles only when lang is en", () => {
    expect(basemap("std", "ja").tiles?.[0]).toContain("/xyz/std/");
    expect(basemap("std", "en").tiles?.[0]).toContain("/xyz/english/");
  });

  it("does NOT swap pale/photo/osm in en (no english variant)", () => {
    expect(basemap("pale", "en").tiles?.[0]).toContain("/xyz/pale/");
    expect(basemap("photo", "en").tiles?.[0]).toContain("/xyz/seamlessphoto/");
    expect(basemap("osm", "en").tiles?.[0]).toContain("tile.openstreetmap.org");
  });

  it("uses a .jpg extension for the photo style", () => {
    expect(basemap("photo", "ja").tiles?.[0]).toMatch(/\.jpg$/);
  });

  it("falls back to the default style (osm) for an unknown id", () => {
    expect(basemap("bogus" as never).tiles?.[0]).toContain("tile.openstreetmap.org");
  });

  it("exposes the four styles in order osm/pale/std/photo", () => {
    expect(MAP_STYLES.map((s) => s.id)).toEqual(["osm", "pale", "std", "photo"]);
  });
});

describe("map style pref (localStorage)", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to pale (GSI 淡色) when nothing stored", () => {
    expect(readMapStylePref()).toBe("pale");
    expect(DEFAULT_MAP_STYLE_ID).toBe("pale");
  });

  it("round-trips a valid id", () => {
    writeMapStylePref("photo");
    expect(readMapStylePref()).toBe("photo");
  });

  it("ignores an unknown stored id and returns the default", () => {
    localStorage.setItem("transit.mapStyle", "satellite-pro");
    expect(readMapStylePref()).toBe("pale");
  });
});

describe("buildThumbnailUrl", () => {
  it("builds a tile URL from the style's own template at the given center/zoom", () => {
    expect(buildThumbnailUrl("pale", "ja", { lng: 139.767, lat: 35.681, zoom: 10 })).toBe(
      "https://cyberjapandata.gsi.go.jp/xyz/pale/10/909/403.png",
    );
  });

  it("clamps the requested zoom to the style's own maxzoom", () => {
    // pale maxzoom is 18; a zoom of 25 must clamp to the z18 tile, not request
    // an unpublished level.
    expect(buildThumbnailUrl("pale", "ja", { lng: 139.767, lat: 35.681, zoom: 25 })).toBe(
      "https://cyberjapandata.gsi.go.jp/xyz/pale/18/232847/103226.png",
    );
  });

  it("uses the english tile template for std only when lang is en", () => {
    const view = { lng: 139.767, lat: 35.681, zoom: 10 };
    expect(buildThumbnailUrl("std", "ja", view)).toContain("/xyz/std/");
    expect(buildThumbnailUrl("std", "en", view)).toContain("/xyz/english/");
  });

  it("uses a .jpg extension for the photo style", () => {
    expect(buildThumbnailUrl("photo", "ja", DEFAULT_THUMBNAIL_VIEW)).toMatch(/\.jpg$/);
  });

  it("falls back to the default catalog entry for an unknown id", () => {
    expect(buildThumbnailUrl("bogus" as never, "ja", DEFAULT_THUMBNAIL_VIEW)).toContain(
      "tile.openstreetmap.org",
    );
  });

  it("has a fallback view landing on real GSI-covered territory", () => {
    expect(buildThumbnailUrl("pale", "ja", DEFAULT_THUMBNAIL_VIEW)).toBe(
      "https://cyberjapandata.gsi.go.jp/xyz/pale/5/28/12.png",
    );
  });
});

describe("map basemap dim pref (localStorage)", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to a calm mid-range amount when nothing stored", () => {
    const amount = readMapDimPref();
    expect(amount).toBeGreaterThan(0);
    expect(amount).toBeLessThanOrEqual(MAX_DIM_AMOUNT);
  });

  it("round-trips a valid amount", () => {
    writeMapDimPref(0.45);
    expect(readMapDimPref()).toBe(0.45);
  });

  it("clamps a stored amount to [0, MAX_DIM_AMOUNT]", () => {
    localStorage.setItem("transit.mapDim", "5");
    expect(readMapDimPref()).toBe(MAX_DIM_AMOUNT);
    localStorage.setItem("transit.mapDim", "-1");
    expect(readMapDimPref()).toBe(0);
  });

  it("ignores a non-numeric stored value and returns the default", () => {
    localStorage.setItem("transit.mapDim", "lots");
    expect(readMapDimPref()).toBeGreaterThan(0);
    expect(readMapDimPref()).toBeLessThanOrEqual(MAX_DIM_AMOUNT);
  });
});
