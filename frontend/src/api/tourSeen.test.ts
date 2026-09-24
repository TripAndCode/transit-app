import { describe, it, expect, beforeEach, vi } from "vitest";
import { readTourSeen, writeTourSeen, resetTourSeenMemoryForTests } from "./tourSeen";

describe("tourSeen (localStorage)", () => {
  beforeEach(() => {
    localStorage.clear();
    resetTourSeenMemoryForTests();
  });

  it("returns 'unseen' when nothing stored", () => {
    expect(readTourSeen()).toBe("unseen");
  });

  it("returns 'seen' after writing", () => {
    writeTourSeen();
    expect(readTourSeen()).toBe("seen");
  });

  it("returns 'unavailable' (not 'unseen') when localStorage.getItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(readTourSeen()).toBe("unavailable");
    spy.mockRestore();
  });

  it("doesn't throw when localStorage.setItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(() => writeTourSeen()).not.toThrow();
    spy.mockRestore();
  });

  it("reports 'unavailable' (not 'unseen') on the next read after a setItem-only failure", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(readTourSeen()).toBe("unseen");
    writeTourSeen();
    spy.mockRestore();
    expect(readTourSeen()).toBe("unavailable");
  });
});
