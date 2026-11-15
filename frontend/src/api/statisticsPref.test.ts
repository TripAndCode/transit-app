import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readShowStatistics, resetShowStatisticsForTests, writeShowStatistics } from "./statisticsPref";

describe("statisticsPref", () => {
  beforeEach(() => {
    localStorage.clear();
    resetShowStatisticsForTests();
  });
  afterEach(() => vi.restoreAllMocks());

  it("is off for a browser that never chose", () => {
    expect(readShowStatistics()).toBe(false);
  });

  it("remembers the choice in this browser", () => {
    writeShowStatistics(true);
    expect(localStorage.getItem("transit.showStatistics")).toBe("1");
    expect(readShowStatistics()).toBe(true);
    writeShowStatistics(false);
    expect(readShowStatistics()).toBe(false);
  });

  it("still switches where storage refuses the write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    writeShowStatistics(true);
    expect(readShowStatistics()).toBe(true);
  });
});
