import { describe, expect, it } from "vitest";
import { routeGroups, selectedGroup } from "./routeGroups";
import type { Route } from "../../api/types";

function route(route_code: string, route_long_name: string): Route {
  return { route_id: route_code, route_code, route_long_name, route_short_name: null, trip_headsigns: [] };
}

describe("routeGroups", () => {
  it("groups routes sharing a base name across differing leading 系統番号 prefixes", () => {
    const routes = [
      route("1673796142", "11-19 共立ハイツ線"),
      route("2613312916", "14H 共立ハイツ線"),
      route("3581531211", "14 共立ハイツ線"),
      route("583208004", "共立ハイツ線"),
    ];
    const groups = routeGroups(routes);
    expect(groups).toHaveLength(1);
    expect(groups[0][0]).toBe("共立ハイツ線");
    expect(groups[0][1].map((r) => r.route_code).sort()).toEqual(
      ["1673796142", "2613312916", "3581531211", "583208004"].sort(),
    );
  });

  it("keeps genuinely different route names as separate groups", () => {
    const routes = [route("1", "14-5 共立ハイツ線"), route("2", "17-1 五月が丘線")];
    const groups = routeGroups(routes);
    expect(groups.map(([name]) => name).sort()).toEqual(["五月が丘線", "共立ハイツ線"]);
  });

  it("reproduces agency 13's real shape: 8 of 11 route_codes collapse into the 共立ハイツ線 line", () => {
    const names = [
      ["1261605731", "11-51 大迫団地線"], ["1673796142", "11-19 共立ハイツ線"],
      ["1741626971", "11 フジハイツ・イトーピア線"], ["2165965154", "17-1 五月が丘・ジアウトレット広島線"],
      ["2613312916", "14H 共立ハイツ線"], ["3377334154", "14-9 共立ハイツ線"],
      ["3581531211", "14 共立ハイツ線"], ["4215053554", "14-2 共立ハイツ線"],
      ["4277339220", "11 共立ハイツ線"], ["444086881", "14-5 共立ハイツ線"], ["583208004", "共立ハイツ線"],
    ] as const;
    const routes = names.map(([code, name]) => route(code, name));
    const groups = routeGroups(routes);
    const kyoritsu = groups.find(([name]) => name === "共立ハイツ線");
    expect(kyoritsu?.[1]).toHaveLength(8);
  });

  it("selectedGroup still resolves correctly once grouping is fixed", () => {
    const routes = [route("a", "14 共立ハイツ線"), route("b", "共立ハイツ線")];
    expect(selectedGroup(routes, ["a"])).toBe("共立ハイツ線");
    expect(selectedGroup(routes, ["a", "b"])).toBe("共立ハイツ線");
  });

  it("skips routes without a route_code", () => {
    const routes: Route[] = [{ route_id: "x", route_code: null, route_long_name: "共立ハイツ線", route_short_name: null, trip_headsigns: [] }];
    expect(routeGroups(routes)).toHaveLength(0);
  });

  it("keeps a name that is entirely a pattern-number token instead of collapsing it to an empty group", () => {
    const routes = [route("a", "14-5"), route("b", "17-1")];
    expect(routeGroups(routes).map(([name]) => name).sort()).toEqual(["14-5", "17-1"]);
  });
});
