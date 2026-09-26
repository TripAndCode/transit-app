import { describe, it, expect } from "vitest";
import { scoreMatch, filterItems } from "./commandPaletteMatch";

describe("scoreMatch", () => {
  it("ranks a prefix match above a plain substring match", () => {
    const prefix = scoreMatch("rep", "Reports")!;
    const substring = scoreMatch("rep", "Segment analysis: reports")!;
    expect(prefix).not.toBeNull();
    expect(substring).not.toBeNull();
    expect(prefix).toBeGreaterThan(substring);
  });

  it("ranks a substring match above a scattered subsequence match", () => {
    const substring = scoreMatch("ort", "Reports")!;
    const subsequence = scoreMatch("ort", "On Route Trend")!; // o..r..t in order, not contiguous
    expect(substring).toBeGreaterThan(subsequence);
  });

  it("matches an in-order, non-contiguous subsequence", () => {
    expect(scoreMatch("rpt", "Reports")).not.toBeNull();
  });

  it("returns null when the query is not a subsequence at all", () => {
    expect(scoreMatch("xyz", "Reports")).toBeNull();
  });

  it("is case-insensitive", () => {
    expect(scoreMatch("REP", "reports")).not.toBeNull();
  });

  it("prefers the shorter of two targets that both prefix-match", () => {
    const short = scoreMatch("42", "42 系統")!;
    const long = scoreMatch("42", "42 系統（長い説明が続く路線名）")!;
    expect(short).toBeGreaterThan(long);
  });
});

describe("filterItems", () => {
  const items = [
    { label: "概況", sublabel: "現在の運行" },
    { label: "区間分析", sublabel: "遅れが増えた区間を調べる" },
    { label: "レポート", sublabel: "期間のまとめ" },
    { label: "42 金沢港〜夕日寺", sublabel: "route_code 42" },
  ];

  it("returns every item, unchanged order, for an empty query", () => {
    expect(filterItems(items, "")).toEqual(items);
    expect(filterItems(items, "   ")).toEqual(items);
  });

  it("keeps only items whose label or sublabel matches the query", () => {
    const result = filterItems(items, "区間");
    expect(result).toEqual([items[1]]);
  });

  it("matches against the sublabel when the label alone would not match", () => {
    const result = filterItems(items, "route_code");
    expect(result).toEqual([items[3]]);
  });

  it("ranks a prefix match on label above a weaker sublabel-only match", () => {
    const result = filterItems(items, "42");
    expect(result[0]).toEqual(items[3]);
  });

  it("excludes items that match neither label nor sublabel", () => {
    expect(filterItems(items, "存在しない")).toEqual([]);
  });
});
