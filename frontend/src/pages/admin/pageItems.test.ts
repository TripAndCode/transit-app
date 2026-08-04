import { describe, it, expect } from "vitest";
import { pageItems } from "./pageItems";

describe("pageItems", () => {
  it("returns every page when totalPages <= 7", () => {
    expect(pageItems(1, 5)).toEqual([1, 2, 3, 4, 5]);
  });

  it("keeps a fixed 3-wide window and never collapses at the first page", () => {
    expect(pageItems(1, 10)).toEqual([1, 2, 3, 4, "ellipsis", 10]);
  });

  it("keeps a fixed 3-wide window and never collapses at the last page", () => {
    expect(pageItems(10, 10)).toEqual([1, "ellipsis", 7, 8, 9, 10]);
  });

  it("centers the window with ellipses on both sides in the middle", () => {
    expect(pageItems(5, 10)).toEqual([1, "ellipsis", 4, 5, 6, "ellipsis", 10]);
  });
});
