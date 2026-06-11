import { describe, it, expect, beforeEach } from "vitest";
import {
  buildStyle,
  MAP_STYLES,
  DEFAULT_MAP_STYLE_ID,
  readMapStylePref,
  writeMapStylePref,
} from "./mapStyle";

describe("buildStyle", () => {
  it("returns a v8 raster style for pale with the GSI pale tiles + attribution", () => {
    const s = buildStyle("pale", "ja");
    expect(s.version).toBe(8);
    const src = (s.sources as Record<string, { tiles?: string[]; attribution?: string }>).gsi;
    expect(src.tiles?.[0]).toContain("/xyz/pale/");
    expect(src.attribution).toContain("国土地理院");
    expect(s.layers[0]).toMatchObject({ type: "raster", source: "gsi" });
  });

  it("swaps std to the english tiles only when lang is en", () => {
    expect((buildStyle("std", "ja").sources as Record<string, { tiles?: string[] }>).gsi.tiles?.[0]).toContain("/xyz/std/");
    expect((buildStyle("std", "en").sources as Record<string, { tiles?: string[] }>).gsi.tiles?.[0]).toContain("/xyz/english/");
  });

  it("does NOT swap pale or photo in en (no english variant)", () => {
    expect((buildStyle("pale", "en").sources as Record<string, { tiles?: string[] }>).gsi.tiles?.[0]).toContain("/xyz/pale/");
    expect((buildStyle("photo", "en").sources as Record<string, { tiles?: string[] }>).gsi.tiles?.[0]).toContain("/xyz/seamlessphoto/");
  });

  it("uses a .jpg extension for the photo style", () => {
    expect((buildStyle("photo", "ja").sources as Record<string, { tiles?: string[] }>).gsi.tiles?.[0]).toMatch(/\.jpg$/);
  });

  it("falls back to the default style for an unknown id", () => {
    const s = buildStyle("bogus" as never, "ja");
    expect((s.sources as Record<string, { tiles?: string[] }>).gsi.tiles?.[0]).toContain("/xyz/pale/");
  });

  it("exposes exactly three styles", () => {
    expect(MAP_STYLES.map((s) => s.id)).toEqual(["pale", "std", "photo"]);
  });
});

describe("map style pref (localStorage)", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to pale when nothing stored", () => {
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
