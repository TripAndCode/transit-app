import { describe, it, expect, beforeEach } from "vitest";
import { MAX_RECENT_AGENCIES, pushRecentAgency, readRecentAgencies } from "./recentAgencies";

describe("recentAgencies", () => {
  beforeEach(() => localStorage.clear());

  it("starts empty", () => {
    expect(readRecentAgencies()).toEqual([]);
  });

  it("puts the most recently switched-to agency first", () => {
    pushRecentAgency(1);
    pushRecentAgency(2);
    expect(readRecentAgencies()).toEqual([2, 1]);
  });

  it("moves an already-visited agency back to the front instead of duplicating it", () => {
    pushRecentAgency(1);
    pushRecentAgency(2);
    pushRecentAgency(1);
    expect(readRecentAgencies()).toEqual([1, 2]);
  });

  it("keeps at most MAX_RECENT_AGENCIES entries", () => {
    for (let id = 1; id <= MAX_RECENT_AGENCIES + 3; id++) pushRecentAgency(id);
    const stored = readRecentAgencies();
    expect(stored).toHaveLength(MAX_RECENT_AGENCIES);
    expect(stored[0]).toBe(MAX_RECENT_AGENCIES + 3);
  });

  it("ignores a corrupt stored value rather than throwing", () => {
    localStorage.setItem("transit.recentAgencies", "{not json");
    expect(readRecentAgencies()).toEqual([]);
  });

  it("drops non-numeric entries from a hand-edited stored value", () => {
    localStorage.setItem("transit.recentAgencies", JSON.stringify([1, "two", null, 3]));
    expect(readRecentAgencies()).toEqual([1, 3]);
  });
});
