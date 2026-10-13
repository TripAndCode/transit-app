import { describe, expect, it } from "vitest";
import { distinctRouteTexts, routeLabelParts, routeLabelPartsByCode, routeNameText } from "./routeDisplayName";
import type { Route } from "./types";

function route(fields: Partial<Route>): Route {
  return { route_id: "r1", route_short_name: null, route_long_name: null, route_code: "r1", trip_headsigns: [], ...fields };
}

function nameOf(fields: Partial<Route>, agencyName?: string) {
  const { plate, line } = routeLabelParts(route(fields), agencyName);
  return { plate, line };
}

describe("rule 1: a leading public code is split off whichever name field holds it", () => {
  it("splits it off the short name (Aomori)", () => {
    expect(nameOf({ route_short_name: "A1 国道・古川線", route_long_name: "" })).toEqual({ plate: "A1", line: "国道・古川線" });
    expect(nameOf({ route_short_name: "L21 中央大橋線", route_long_name: "" })).toEqual({ plate: "L21", line: "中央大橋線" });
  });

  it("splits it off the long name when the short name is empty (HD西広島)", () => {
    expect(nameOf({ route_short_name: "", route_long_name: "17-1 五月が丘・ジアウトレット広島線" })).toEqual({
      plate: "17-1",
      line: "五月が丘・ジアウトレット広島線",
    });
  });

  it("draws a short name that is itself a code as the plate, beside the long name (Hiroden)", () => {
    expect(nameOf({ route_short_name: "70-3", route_long_name: "あさひが丘線" })).toEqual({ plate: "70-3", line: "あさひが丘線" });
    expect(nameOf({ route_short_name: "T50", route_long_name: "石江・新城線" })).toEqual({ plate: "T50", line: "石江・新城線" });
  });

  it("shows the plate alone when the long name is empty or the same as the short name", () => {
    expect(nameOf({ route_short_name: "70-3", route_long_name: "" })).toEqual({ plate: "70-3", line: null });
    expect(nameOf({ route_short_name: "9", route_long_name: "9" })).toEqual({ plate: "9", line: null });
  });
});

describe("rule 2: a plate holds only a short public code, at most 6 characters with a digit in it", () => {
  it("keeps an internal id out of the plate and out of the name (呉市生活バス)", () => {
    expect(nameOf({ route_short_name: "904-14-01", route_long_name: "呉市生活バス 阿賀音戸の瀬戸線" }, "呉市生活バス")).toEqual({
      plate: null,
      line: "阿賀音戸の瀬戸線",
    });
  });

  it("keeps a brand out of the plate (JR中国)", () => {
    expect(nameOf({ route_short_name: "めいぷる～ぷ", route_long_name: "" })).toEqual({ plate: null, line: "めいぷる～ぷ" });
  });

  it("never draws a feed's opaque numeric id as a plate", () => {
    expect(nameOf({ route_short_name: "1471981638" })).toEqual({ plate: null, line: "1471981638" });
  });
});

describe("rule 3: a long name that already holds the short name is shown alone", () => {
  it("drops the plate when the long name contains a brand short name (JR中国)", () => {
    expect(nameOf({ route_short_name: "めいぷる～ぷ", route_long_name: "めいぷる～ぷ（新天地経由）" })).toEqual({
      plate: null,
      line: "めいぷる～ぷ（新天地経由）",
    });
  });

  it("drops the plate when the long name contains the short code (Hiroden)", () => {
    expect(nameOf({ route_short_name: "12", route_long_name: "12号線" })).toEqual({ plate: null, line: "12号線" });
  });

  it("counts a short code only as a whole token of the long name", () => {
    expect(nameOf({ route_short_name: "1", route_long_name: "Line 10" })).toEqual({ plate: "1", line: "Line 10" });
    expect(nameOf({ route_short_name: "2", route_long_name: "12号線" })).toEqual({ plate: "2", line: "12号線" });
  });

  it("keeps the plate when the code was split off the long name itself", () => {
    expect(nameOf({ route_short_name: "14-5", route_long_name: "14-5 共立ハイツ線" })).toEqual({ plate: "14-5", line: "共立ハイツ線" });
  });
});

describe("rule 4: a leading agency name is stripped from the line name", () => {
  it("strips the exact agency name (呉市生活バス)", () => {
    expect(nameOf({ route_short_name: "", route_long_name: "呉市生活バス 阿賀音戸の瀬戸線" }, "呉市生活バス")).toEqual({
      plate: null,
      line: "阿賀音戸の瀬戸線",
    });
  });

  it("leaves a brand that differs from the agency name alone (廿日市)", () => {
    expect(nameOf({ route_short_name: "", route_long_name: "廿日市さくらバス 原ルート" }, "廿日市市")).toEqual({
      plate: null,
      line: "廿日市さくらバス 原ルート",
    });
  });

  it("keeps a name that is nothing but the agency name", () => {
    expect(nameOf({ route_short_name: "", route_long_name: "呉市生活バス" }, "呉市生活バス")).toEqual({ plate: null, line: "呉市生活バス" });
  });
});

describe("the name's fallbacks", () => {
  it("shows the long name alone when there is no short name", () => {
    expect(nameOf({ route_short_name: "", route_long_name: "国道・古川線" })).toEqual({ plate: null, line: "国道・古川線" });
  });

  it("keeps a short name that is not a code as the name", () => {
    expect(nameOf({ route_short_name: "観光通り線" })).toEqual({ plate: null, line: "観光通り線" });
    expect(nameOf({ route_short_name: "Line 1", route_long_name: "Coast" })).toEqual({ plate: null, line: "Line 1 Coast" });
  });

  it("falls back to the route_id when neither name field is set", () => {
    expect(nameOf({ route_id: "国道・古川線(1021)", route_short_name: "", route_long_name: "" })).toEqual({
      plate: null,
      line: "国道・古川線(1021)",
    });
  });

  it("carries the route_id for a tooltip", () => {
    expect(routeLabelParts(route({ route_id: "1471981638", route_short_name: "70-3" })).id).toBe("1471981638");
  });
});

describe("rule 5: the terminals", () => {
  it("runs one way from origin to destination, with the via point", () => {
    expect(routeLabelParts(route({ origin_name: "広島バスセンター", via_name: "市役所", destination_name: "山田団地車庫前" })).terminals).toEqual({
      from: "広島バスセンター",
      to: "山田団地車庫前",
      via: "市役所",
      loop: false,
      bidirectional: false,
    });
  });

  it("marks a route that runs both ways", () => {
    expect(routeLabelParts(route({ origin_name: "広島駅", destination_name: "観音新町三丁目", bidirectional: true })).terminals).toMatchObject({
      from: "広島駅",
      to: "観音新町三丁目",
      bidirectional: true,
    });
  });

  it("marks a loop, which needs only its origin", () => {
    expect(routeLabelParts(route({ origin_name: "呉駅前", is_loop: true })).terminals).toMatchObject({ from: "呉駅前", loop: true });
  });

  it("has no terminals until the feed gives both ends", () => {
    expect(routeLabelParts(route({ origin_name: "広島駅" })).terminals).toBeNull();
    expect(routeLabelParts(route({})).terminals).toBeNull();
  });
});

describe("rule 6: a headsign only tells apart routes that would otherwise read the same", () => {
  it("adds each route's headsign as a plain qualifier when two labels match (Aomori)", () => {
    const parts = routeLabelPartsByCode([
      route({ route_id: "a", route_code: "a", route_short_name: "横内環状線", trip_headsigns: ["中筒井線"] }),
      route({ route_id: "b", route_code: "b", route_short_name: "横内環状線", trip_headsigns: ["観光通り線"] }),
    ]);
    expect(parts.get("a")?.qualifier).toEqual({ text: "中筒井線", via: false });
    expect(parts.get("b")?.qualifier).toEqual({ text: "観光通り線", via: false });
  });

  it("reads a headsign ending in 経由 as a via point (Hiroshima feeds)", () => {
    const parts = routeLabelPartsByCode([
      route({ route_id: "a", route_code: "a", route_short_name: "54-1", route_long_name: "西広島バイパス線", trip_headsigns: ["市役所経由"] }),
      route({ route_id: "b", route_code: "b", route_short_name: "54-1", route_long_name: "西広島バイパス線", trip_headsigns: ["己斐経由"] }),
    ]);
    expect(parts.get("a")?.qualifier).toEqual({ text: "市役所", via: true });
    expect(parts.get("b")?.qualifier).toEqual({ text: "己斐", via: true });
  });

  it("reads a destination headsign as it is, never adding a 'bound for' (agency 54)", () => {
    const parts = routeLabelPartsByCode([
      route({ route_id: "a", route_code: "a", route_short_name: "3", trip_headsigns: ["フジ熊野店"] }),
      route({ route_id: "b", route_code: "b", route_short_name: "3", trip_headsigns: ["熊野営業所"] }),
    ]);
    expect(parts.get("a")?.qualifier).toEqual({ text: "フジ熊野店", via: false });
  });

  it("strips the code prefix and line name an Aomori headsign carries", () => {
    const parts = routeLabelPartsByCode([
      route({ route_id: "a", route_code: "a", route_short_name: "A1 国道・古川線", trip_headsigns: ["A1青森駅（国道・古川線）"] }),
      route({ route_id: "b", route_code: "b", route_short_name: "A1 国道・古川線", trip_headsigns: ["A1明の星→青森駅（国道・古川線）"] }),
    ]);
    expect(parts.get("a")?.qualifier).toEqual({ text: "青森駅", via: false });
    expect(parts.get("b")?.qualifier).toEqual({ text: "明の星→青森駅", via: false });
  });

  it("leaves a route whose label is already its own without a qualifier", () => {
    const parts = routeLabelPartsByCode([
      route({ route_id: "a", route_code: "a", route_short_name: "70-3", route_long_name: "あさひが丘線", trip_headsigns: ["市役所経由"] }),
      route({ route_id: "b", route_code: "b", route_short_name: "54-1", route_long_name: "西広島バイパス線", trip_headsigns: ["市役所経由"] }),
    ]);
    expect(parts.get("a")?.qualifier).toBeNull();
    expect(parts.get("b")?.qualifier).toBeNull();
  });

  it("does not use a headsign when terminals already tell two routes apart", () => {
    const parts = routeLabelPartsByCode([
      route({ route_id: "a", route_code: "a", route_short_name: "2-5", origin_name: "県庁前", destination_name: "府中ニュータウン", trip_headsigns: ["x"] }),
      route({ route_id: "b", route_code: "b", route_short_name: "2-5", origin_name: "県庁前", destination_name: "府中永田転回場", trip_headsigns: ["y"] }),
    ]);
    expect(parts.get("a")?.qualifier).toBeNull();
  });

  it("adds nothing when a route has several headsigns, or the matching routes share one", () => {
    const parts = routeLabelPartsByCode([
      route({ route_id: "a", route_code: "a", route_short_name: "3", trip_headsigns: ["新田行", "古川行"] }),
      route({ route_id: "b", route_code: "b", route_short_name: "3", trip_headsigns: ["新田行"] }),
      route({ route_id: "c", route_code: "c", route_short_name: "5", trip_headsigns: ["新田行"] }),
      route({ route_id: "d", route_code: "d", route_short_name: "5", trip_headsigns: ["新田行"] }),
    ]);
    expect(parts.get("a")?.qualifier).toBeNull();
    expect(parts.get("b")?.qualifier).toEqual({ text: "新田行", via: false });
    expect(parts.get("c")?.qualifier).toBeNull();
    expect(parts.get("d")?.qualifier).toBeNull();
  });
});

describe("routes that share a name", () => {
  it("marks each route whose plate and line name another route also has", () => {
    const parts = routeLabelPartsByCode([
      route({ route_id: "a", route_code: "a", route_short_name: "A1 国道・古川線", trip_headsigns: ["A1明の星→青森駅（国道・古川線）"] }),
      route({ route_id: "b", route_code: "b", route_short_name: "A1 国道・古川線", trip_headsigns: ["A1中央大橋→青森駅（国道・古川線）"] }),
      route({ route_id: "c", route_code: "c", route_short_name: "L21 中央大橋線" }),
    ]);
    expect(parts.get("a")?.ambiguous).toBe(true);
    expect(parts.get("b")?.ambiguous).toBe(true);
    expect(parts.get("c")?.ambiguous).toBe(false);
  });

  it("marks routes told apart only by their terminals, so a one-line label keeps them apart too", () => {
    const parts = routeLabelPartsByCode([
      route({ route_id: "a", route_code: "a", route_short_name: "2-5", route_long_name: "2号線", origin_name: "県庁前", destination_name: "府中ニュータウン" }),
      route({ route_id: "b", route_code: "b", route_short_name: "2-5", route_long_name: "2号線", origin_name: "県庁前", destination_name: "府中永田転回場" }),
    ]);
    expect(parts.get("a")?.ambiguous).toBe(true);
    expect(parts.get("a")?.qualifier).toBeNull();
  });
});

describe("routeNameText", () => {
  it("reads as the plate and the line name on one line", () => {
    expect(routeNameText(routeLabelParts(route({ route_short_name: "70-3", route_long_name: "あさひが丘線" })))).toBe("70-3 あさひが丘線");
    expect(routeNameText(routeLabelParts(route({ route_short_name: "70-3" })))).toBe("70-3");
    expect(routeNameText(routeLabelParts(route({ route_long_name: "国道・古川線" })))).toBe("国道・古川線");
    expect(routeNameText(routeLabelParts(route({ route_short_name: "A1 国道・古川線" })))).toBe("A1 国道・古川線");
  });
});

describe("distinctRouteTexts", () => {
  it("names each route, adding where it runs only where two names match, and the route_id only where that still leaves two alike", () => {
    const labels = distinctRouteTexts([
      { key: "a", name: "70-3 あさひが丘線", full: "70-3 あさひが丘線 · 広島バスセンター → あさひが丘下", id: "1471981638" },
      { key: "b", name: "2-5 2号線", full: "2-5 2号線 · 県庁前 → 府中ニュータウン", id: "3968526772" },
      { key: "c", name: "2-5 2号線", full: "2-5 2号線 · 県庁前 → 府中永田転回場", id: "1757472590" },
      { key: "d", name: "12号線", full: "12号線", id: "111" },
      { key: "e", name: "12号線", full: "12号線", id: "222" },
    ]);
    expect(labels.get("a")).toBe("70-3 あさひが丘線");
    expect(labels.get("b")).toBe("2-5 2号線 · 県庁前 → 府中ニュータウン");
    expect(labels.get("c")).toBe("2-5 2号線 · 県庁前 → 府中永田転回場");
    expect(labels.get("d")).toBe("12号線 (111)");
    expect(labels.get("e")).toBe("12号線 (222)");
  });
});
