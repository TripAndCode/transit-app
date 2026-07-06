import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { readThemePref, writeThemePref, applyTheme } from "./theme";

describe("theme preference (localStorage)", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    delete document.documentElement.dataset.theme;
  });

  it("defaults to dark when nothing stored", () => {
    expect(readThemePref()).toBe("dark");
  });

  it("round-trips a stored value", () => {
    writeThemePref("light");
    expect(readThemePref()).toBe("light");
  });

  it("ignores an invalid stored value and returns the default", () => {
    localStorage.setItem("transit.theme", "sepia");
    expect(readThemePref()).toBe("dark");
  });

  it("returns dark when localStorage.getItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(readThemePref()).toBe("dark");
    spy.mockRestore();
  });

  it("doesn't throw when localStorage.setItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(() => writeThemePref("light")).not.toThrow();
    spy.mockRestore();
  });

  it("applyTheme sets data-theme on the html element", () => {
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
