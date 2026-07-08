import { describe, it, expect, beforeEach, vi } from "vitest";
import { readLastAgency, writeLastAgency } from "./lastAgency";

describe("lastAgency (localStorage)", () => {
  beforeEach(() => localStorage.clear());

  it("returns null when nothing stored", () => {
    expect(readLastAgency()).toBeNull();
  });

  it("round-trips a stored id", () => {
    writeLastAgency(42);
    expect(readLastAgency()).toBe(42);
  });

  it("returns null for a non-numeric stored value", () => {
    localStorage.setItem("transit.lastAgency", "not-a-number");
    expect(readLastAgency()).toBeNull();
  });

  it("returns null when localStorage.getItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(readLastAgency()).toBeNull();
    spy.mockRestore();
  });

  it("doesn't throw when localStorage.setItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(() => writeLastAgency(1)).not.toThrow();
    spy.mockRestore();
  });
});
