import { describe, it, expect } from "vitest";
import i18n from "../../i18n";
import { buildNextStepChips } from "./nextStepChips";

const t = i18n.getFixedT("en");

describe("buildNextStepChips", () => {
  it("returns no chips for a dispatch-free (LLM follow-up) message", () => {
    expect(
      buildNextStepChips({ tool: null, args: null, conditions: null, resultKind: "text", t }),
    ).toEqual([]);
  });

  it("offers the morning-peak filter chip for a dispatched message not already scoped to morning", () => {
    const chips = buildNextStepChips({
      tool: "top_n",
      args: {},
      conditions: { dow: "all", time_band: "all", service: "all" },
      resultKind: "table",
      t,
    });
    const chip = chips.find((c) => c.action.kind === "filter");
    expect(chip?.action).toEqual({ kind: "filter", patch: { time_band: "morning" } });
  });

  it("omits the morning-peak chip when already scoped to morning", () => {
    const chips = buildNextStepChips({
      tool: "top_n",
      args: {},
      conditions: { dow: "all", time_band: "morning", service: "all" },
      resultKind: "table",
      t,
    });
    expect(chips.some((c) => c.action.kind === "filter")).toBe(false);
  });

  it("offers the map chip when args carries a route (either 'route' or 'route_code')", () => {
    const withRoute = buildNextStepChips({
      tool: "segment_hotspots",
      args: { route: "A05" },
      conditions: null,
      resultKind: "table",
      t,
    });
    expect(withRoute.find((c) => c.action.kind === "map")?.action).toEqual({ kind: "map", route: "A05" });

    const withRouteCode = buildNextStepChips({
      tool: "route_stats",
      args: { route_code: "K12" },
      conditions: null,
      resultKind: "kv",
      t,
    });
    expect(withRouteCode.find((c) => c.action.kind === "map")?.action).toEqual({ kind: "map", route: "K12" });
  });

  it("omits the map chip when args has no route", () => {
    const chips = buildNextStepChips({ tool: "top_n", args: {}, conditions: null, resultKind: "table", t });
    expect(chips.some((c) => c.action.kind === "map")).toBe(false);
  });

  it("offers the compare-previous chip when args has an explicit from_date/to_date window", () => {
    const chips = buildNextStepChips({
      tool: "time_series",
      args: { from_date: "2026-08-15", to_date: "2026-08-28" },
      conditions: null,
      resultKind: "series",
      t,
    });
    expect(chips.find((c) => c.action.kind === "compare_previous")?.action).toEqual({
      kind: "compare_previous",
      tool: "time_series",
      args: { from_date: "2026-08-15", to_date: "2026-08-28" },
    });
  });

  it("omits the compare-previous chip without an explicit date window", () => {
    const chips = buildNextStepChips({ tool: "top_n", args: {}, conditions: null, resultKind: "table", t });
    expect(chips.some((c) => c.action.kind === "compare_previous")).toBe(false);
  });

  it("offers the PNG export chip only for a series (chart) result", () => {
    const series = buildNextStepChips({ tool: "time_series", args: {}, conditions: null, resultKind: "series", t });
    expect(series.some((c) => c.action.kind === "export_png")).toBe(true);

    const table = buildNextStepChips({ tool: "top_n", args: {}, conditions: null, resultKind: "table", t });
    expect(table.some((c) => c.action.kind === "export_png")).toBe(false);
  });

  it("orders chips: filter, map, compare, export", () => {
    const chips = buildNextStepChips({
      tool: "time_series",
      args: { route: "A05", from_date: "2026-08-15", to_date: "2026-08-28" },
      conditions: { dow: "all", time_band: "all", service: "all" },
      resultKind: "series",
      t,
    });
    expect(chips.map((c) => c.action.kind)).toEqual(["filter", "map", "compare_previous", "export_png"]);
  });

  it("gives every chip a non-empty, verb-phrased label", () => {
    const chips = buildNextStepChips({
      tool: "time_series",
      args: { route: "A05", from_date: "2026-08-15", to_date: "2026-08-28" },
      conditions: null,
      resultKind: "series",
      t,
    });
    for (const chip of chips) expect(chip.label.length).toBeGreaterThan(0);
  });
});
