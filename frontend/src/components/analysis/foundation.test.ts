// @vitest-environment node
import { describe, expect, it } from "vitest";
import { csvText } from "./csv";
import { routeGroups, selectedGroup } from "./routeGroups";
import { routeLabelPartsByCode } from "../../api/routeDisplayName";

describe("export and route scope", () => {
  it("quotes Japanese text, embedded newlines and formulas without changing negative numbers", () => {
    expect(csvText([["停留所,北", 'a"b\nc', "=1+1", -2, null]])).toBe('\ufeff"停留所,北","a""b\nc","\'=1+1","-2",""');
  });
  it("groups published names, never code prefixes or weekday service types", () => {
    const routes = [
      { route_id: "a", route_code: "101", route_long_name: "海岸線", route_short_name: "1", trip_headsigns: [] },
      { route_id: "b", route_code: "999", route_long_name: "海岸線", route_short_name: "9", trip_headsigns: [] },
      { route_id: "c", route_code: "102", route_long_name: "山線", route_short_name: "1", trip_headsigns: [] },
    ];
    const labels = routeLabelPartsByCode(routes);
    expect(routeGroups(routes, labels)).toHaveLength(2);
    expect(selectedGroup(routes, ["101", "999"], labels)).toBe("海岸線");
    expect(selectedGroup(routes, ["101", "102"], labels)).toBe("");
    expect(selectedGroup(routes, [], labels)).toBe("");
  });
});
