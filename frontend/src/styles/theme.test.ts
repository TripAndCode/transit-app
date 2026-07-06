import { describe, it, expect, beforeEach, afterEach } from "vitest";
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

  it("applyTheme sets data-theme on the html element", () => {
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
