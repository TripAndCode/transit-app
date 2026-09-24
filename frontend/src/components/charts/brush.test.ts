import { describe, it, expect } from "vitest";
import { brushIndices, brushRange } from "./brush";

const days = [
  { date: "2026-05-18" },
  { date: "2026-05-19" },
  { date: "2026-05-20" },
  { date: "2026-05-21" },
];

describe("brushRange", () => {
  it("returns the dates spanned by a left-to-right drag", () => {
    expect(brushRange(1, 3, days)).toEqual({ from: "2026-05-19", to: "2026-05-21" });
  });

  it("orders a reversed (right-to-left) drag the same way", () => {
    expect(brushRange(3, 1, days)).toEqual({ from: "2026-05-19", to: "2026-05-21" });
  });

  it("collapses a single-day drag to a one-day range", () => {
    expect(brushRange(2, 2, days)).toEqual({ from: "2026-05-20", to: "2026-05-20" });
  });

  it("clamps indices that ran off either end of the series", () => {
    expect(brushRange(-5, 99, days)).toEqual({ from: "2026-05-18", to: "2026-05-21" });
  });

  it("returns null for an empty series", () => {
    expect(brushRange(0, 0, [])).toBeNull();
  });

  it("returns null rather than indexing with a non-finite bound", () => {
    expect(brushRange(Number.NaN, 2, days)).toBeNull();
  });
});

describe("brushIndices", () => {
  it("enumerates the inclusive span of a drag", () => {
    expect(brushIndices(1, 3)).toEqual([1, 2, 3]);
  });

  it("enumerates a reversed drag in ascending order", () => {
    expect(brushIndices(3, 1)).toEqual([1, 2, 3]);
  });

  it("returns the single index of a zero-length drag", () => {
    expect(brushIndices(2, 2)).toEqual([2]);
  });
});
