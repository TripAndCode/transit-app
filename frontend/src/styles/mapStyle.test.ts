import { describe, it, expect, beforeEach } from "vitest";
import {
  buildStyle,
  MAP_STYLES,
  DEFAULT_MAP_STYLE_ID,
  readMapStylePref,
  writeMapStylePref,
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

  it("defaults to osm when nothing stored", () => {
    expect(readMapStylePref()).toBe("osm");
    expect(DEFAULT_MAP_STYLE_ID).toBe("osm");
  });

  it("round-trips a valid id", () => {
    writeMapStylePref("photo");
    expect(readMapStylePref()).toBe("photo");
  });

  it("ignores an unknown stored id and returns the default", () => {
    localStorage.setItem("transit.mapStyle", "satellite-pro");
    expect(readMapStylePref()).toBe("osm");
  });
});
