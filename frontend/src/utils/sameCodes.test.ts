import { describe, it, expect } from "vitest";
import { sameCodes } from "./sameCodes";

describe("sameCodes", () => {
  it("is true for identical arrays", () => {
    expect(sameCodes(["a", "b"], ["a", "b"])).toBe(true);
  });

  it("ignores order", () => {
    expect(sameCodes(["a", "b"], ["b", "a"])).toBe(true);
  });

  it("is false when lengths differ", () => {
    expect(sameCodes(["a"], ["a", "b"])).toBe(false);
  });

  it("is false when contents differ at the same length", () => {
    expect(sameCodes(["a", "b"], ["a", "c"])).toBe(false);
  });

  it("is true for two empty arrays", () => {
    expect(sameCodes([], [])).toBe(true);
  });
});
