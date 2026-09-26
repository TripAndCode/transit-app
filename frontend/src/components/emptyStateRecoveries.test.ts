import { describe, it, expect, vi } from "vitest";
import i18n from "../i18n";
import { buildFilterCtxReasons, buildFilterCtxRecoveries } from "./emptyStateRecoveries";

const t = i18n.getFixedT("en");
// A caller whose own `useTranslation("design")` default namespace is NOT
// "translation" (AnalysisTab, RouteAnalysisTab, ReportsHomeTab) must still
// resolve real copy, not the bare key -- this module always targets the
// "translation" namespace explicitly regardless of the caller's default.
const tDesignNs = i18n.getFixedT("en", "design");

describe("buildFilterCtxReasons", () => {
  it("returns no reasons when every dimension is at its default", () => {
    expect(buildFilterCtxReasons({ service: "all", routes: [], dow: "all", time_band: "all" }, t)).toEqual([]);
  });

  it("names each non-default dimension", () => {
    const reasons = buildFilterCtxReasons(
      { service: "平日", routes: ["A05", "K12"], dow: "weekend", time_band: "morning" },
      t,
    );
    expect(reasons).toEqual([
      "Day: Weekend/Holiday",
      "Time band: Morning (05–09)",
      "Service: Weekday",
      "Route: A05, K12",
    ]);
  });
});

describe("buildFilterCtxRecoveries", () => {
  it("offers no recoveries when nothing is filtered and there is no latest-data jump", () => {
    const recoveries = buildFilterCtxRecoveries({
      ctx: { service: "all", routes: [] },
      onClearRoutes: vi.fn(),
      onResetService: vi.fn(),
      jumpToLatestData: null,
      t,
    });
    expect(recoveries).toEqual([]);
  });

  it("offers clear-routes only when routes are set", () => {
    const onClearRoutes = vi.fn();
    const recoveries = buildFilterCtxRecoveries({
      ctx: { service: "all", routes: ["A05"] },
      onClearRoutes,
      onResetService: vi.fn(),
      jumpToLatestData: null,
      t,
    });
    expect(recoveries).toHaveLength(1);
    recoveries[0].onClick();
    expect(onClearRoutes).toHaveBeenCalledTimes(1);
  });

  it("offers reset-service only when service is not 'all'", () => {
    const onResetService = vi.fn();
    const recoveries = buildFilterCtxRecoveries({
      ctx: { service: "平日", routes: [] },
      onClearRoutes: vi.fn(),
      onResetService,
      jumpToLatestData: null,
      t,
    });
    expect(recoveries).toHaveLength(1);
    recoveries[0].onClick();
    expect(onResetService).toHaveBeenCalledTimes(1);
  });

  it("offers jump-to-latest-data only when a jump callback is given", () => {
    const jumpToLatestData = vi.fn();
    const recoveries = buildFilterCtxRecoveries({
      ctx: { service: "all", routes: [] },
      onClearRoutes: vi.fn(),
      onResetService: vi.fn(),
      jumpToLatestData,
      t,
    });
    expect(recoveries).toHaveLength(1);
    recoveries[0].onClick();
    expect(jumpToLatestData).toHaveBeenCalledTimes(1);
  });

  it("orders clear-routes, reset-service, jump-to-latest-data and caps at three", () => {
    const recoveries = buildFilterCtxRecoveries({
      ctx: { service: "平日", routes: ["A05"] },
      onClearRoutes: vi.fn(),
      onResetService: vi.fn(),
      jumpToLatestData: vi.fn(),
      t,
    });
    expect(recoveries.map((r) => r.label)).toEqual([
      "Clear the route filter",
      "Reset service type to all",
      "Jump to the latest data",
    ]);
  });

  it("resolves real copy (not the bare key) for a caller bound to a non-translation default namespace", () => {
    const recoveries = buildFilterCtxRecoveries({
      ctx: { service: "平日", routes: ["A05"] },
      onClearRoutes: vi.fn(),
      onResetService: vi.fn(),
      jumpToLatestData: vi.fn(),
      t: tDesignNs,
    });
    expect(recoveries.map((r) => r.label)).toEqual([
      "Clear the route filter",
      "Reset service type to all",
      "Jump to the latest data",
    ]);
    expect(buildFilterCtxReasons({ service: "平日", dow: "weekend" }, tDesignNs)).toEqual([
      "Day: Weekend/Holiday",
      "Service: Weekday",
    ]);
  });
});
