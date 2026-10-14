import { describe, it, expect, vi, afterEach } from "vitest";
import { render, renderHook, screen } from "@testing-library/react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../i18n";
import { useRouteNames } from "./useRouteNames";
import * as hooks from "./hooks";
import type { Route } from "./types";

void i18n.changeLanguage("en");

function mockRoutes(data: Route[] | undefined, isLoading = false, agencyName = "Test Bus") {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data, isLoading } as never);
  vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [{ agency_id: 1, agency_name: agencyName }] } as never);
}

function setupHook() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return renderHook(() => useRouteNames(1), {
    wrapper: ({ children }) =>
      createElement(
        QueryClientProvider,
        { client: queryClient },
        createElement(I18nextProvider, { i18n }, children),
      ),
  });
}

describe("useRouteNames", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("names a route by its code and its line name", () => {
    mockRoutes([
      { route_id: "T50線(39061)", route_short_name: "T50", route_long_name: "石江・新城線", route_code: "39061", trip_headsigns: [] },
    ]);
    const { result } = setupHook();
    expect(result.current.format("39061")).toBe("T50 石江・新城線");
    expect(result.current.parts.get("39061")).toMatchObject({ plate: "T50", line: "石江・新城線", id: "T50線(39061)" });
  });

  it("falls back to route_long_name when route_short_name is blank", () => {
    // Real GTFS data allows either name field to be empty; only route_id is
    // guaranteed non-blank.
    mockRoutes([
      { route_id: "国道・古川線(1021)", route_short_name: "", route_long_name: "国道・古川線", route_code: "1021", trip_headsigns: [] },
    ]);
    const { result } = setupHook();
    expect(result.current.format("1021")).toBe("国道・古川線");
  });

  it("falls back to route_id when both name fields are blank", () => {
    mockRoutes([
      { route_id: "国道・古川線(1021)", route_short_name: "", route_long_name: "", route_code: "1021", trip_headsigns: [] },
    ]);
    const { result } = setupHook();
    expect(result.current.format("1021")).toBe("国道・古川線(1021)");
  });

  it("shows the bare-code fallback when no static route matches", () => {
    // Genuine miss (e.g. a static-data gap for that route_code): no name
    // field is available at all, so the localized "Route {{code}}" template
    // is the correct, honest fallback.
    mockRoutes([
      { route_id: "T50線(39061)", route_short_name: "T50", route_long_name: "石江・新城線", route_code: "39061", trip_headsigns: [] },
    ]);
    const { result } = setupHook();
    expect(result.current.format("53011")).toBe("Route 53011");
    expect(result.current.describe("53011")).toBe("Route 53011");
  });

  it("tells variants of one line apart by their headsigns, and leaves a route that reads as its own alone", () => {
    mockRoutes([
      { route_id: "国道・古川線(10012)", route_short_name: "A1 国道・古川線", route_long_name: "", route_code: "10012", trip_headsigns: ["A1明の星→青森駅（国道・古川線）"] },
      { route_id: "国道・古川線(12212)", route_short_name: "A1 国道・古川線", route_long_name: "", route_code: "12212", trip_headsigns: ["A1中央大橋→青森駅（国道・古川線）"] },
      { route_id: "中央大橋線(12211)", route_short_name: "L21 中央大橋線", route_long_name: "", route_code: "12211", trip_headsigns: ["L21戸山団地（中央大橋線）"] },
    ]);
    const { result } = setupHook();
    // The variants share a name, so even the one-line name says where each runs.
    expect(result.current.format("10012")).toBe("A1 国道・古川線 · 明の星→青森駅");
    expect(result.current.format("12212")).toBe("A1 国道・古川線 · 中央大橋→青森駅");
    expect(result.current.data.get("10012")).toBe("A1 国道・古川線");
    expect(result.current.format("12211")).toBe("L21 中央大橋線");
    expect(result.current.describe("10012")).toBe("A1 国道・古川線 · 明の星→青森駅");
    expect(result.current.describe("12212")).toBe("A1 国道・古川線 · 中央大橋→青森駅");
    expect(result.current.describe("12211")).toBe("L21 中央大橋線");
  });

  it("names the terminals and the via point when the feed gives them", async () => {
    const hiroden: Route[] = [
      { route_id: "1237145910", route_short_name: "54-1", route_long_name: "西広島バイパス線", route_code: "1237145910", trip_headsigns: ["市役所経由"], origin_name: "広島バスセンター", via_name: "市役所", destination_name: "山田団地車庫前" },
      { route_id: "360868713", route_short_name: "3", route_long_name: "広島駅－観音線", route_code: "360868713", trip_headsigns: [], origin_name: "広島駅", destination_name: "観音新町三丁目", bidirectional: true },
      { route_id: "488396710", route_short_name: "11", route_long_name: "呉駅前循環線", route_code: "488396710", trip_headsigns: [], origin_name: "呉駅前", is_loop: true },
      { route_id: "1471981638", route_short_name: "70-3", route_long_name: "あさひが丘線", route_code: "1471981638", trip_headsigns: [] },
    ];
    mockRoutes(hiroden);
    const { result } = setupHook();
    expect(result.current.describe("1237145910")).toBe("54-1 西広島バイパス線 · 広島バスセンター → 山田団地車庫前 · via 市役所");
    expect(result.current.describe("360868713")).toBe("3 広島駅－観音線 · 広島駅 ⇄ 観音新町三丁目");
    expect(result.current.describe("488396710")).toBe("11 呉駅前循環線 · Loop from 呉駅前");
    expect(result.current.describe("1471981638")).toBe("70-3 あさひが丘線");
    await i18n.changeLanguage("ja");
    const ja = setupHook();
    expect(ja.result.current.describe("1237145910")).toBe("54-1 西広島バイパス線 · 広島バスセンター → 山田団地車庫前 · 市役所経由");
    expect(ja.result.current.describe("488396710")).toBe("11 呉駅前循環線 · 呉駅前発着（循環）");
    await i18n.changeLanguage("en");
  });

  it("never adds a destination suffix to a headsign, and reads one ending in 経由 as a via point", async () => {
    await i18n.changeLanguage("ja");
    mockRoutes([
      { route_id: "a", route_short_name: "54-1", route_long_name: "", route_code: "a", trip_headsigns: ["市役所経由"] },
      { route_id: "b", route_short_name: "54-1", route_long_name: "", route_code: "b", trip_headsigns: ["己斐経由"] },
      { route_id: "c", route_short_name: "3", route_long_name: "", route_code: "c", trip_headsigns: ["フジ熊野店"] },
      { route_id: "d", route_short_name: "3", route_long_name: "", route_code: "d", trip_headsigns: ["熊野営業所"] },
    ]);
    const { result } = setupHook();
    expect(result.current.describe("a")).toBe("54-1 · 市役所経由");
    expect(result.current.describe("c")).toBe("3 · フジ熊野店");
    await i18n.changeLanguage("en");
    const en = setupHook();
    expect(en.result.current.describe("a")).toBe("54-1 · via 市役所");
  });

  it("strips the agency's own name from the front of a line name", () => {
    mockRoutes(
      [{ route_id: "k1", route_short_name: "904-14-01", route_long_name: "呉市生活バス 阿賀音戸の瀬戸線", route_code: "k1", trip_headsigns: [] }],
      false,
      "呉市生活バス",
    );
    const { result } = setupHook();
    expect(result.current.format("k1")).toBe("阿賀音戸の瀬戸線");
  });

  it("returns an em dash for a null/undefined route_code", () => {
    mockRoutes([]);
    const { result } = setupHook();
    expect(result.current.format(null)).toBe("—");
    expect(result.current.format(undefined)).toBe("—");
  });
});

// JSX-consumer coverage for useRouteNames' `data`/`isLoading` passthrough,
// beside the plain-renderHook tests above of the `format()` fallback order.
function Probe({ agencyId }: { agencyId: number }) {
  const { data, isLoading, format } = useRouteNames(agencyId);
  if (isLoading) return <span>loading</span>;
  return (
    <span data-testid="probe">
      {format("39061")}|{data.size}
    </span>
  );
}

function setup(agencyId = 1) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <Probe agencyId={agencyId} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("useRouteNames (JSX consumer)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reflects useRoutes' isLoading state before data arrives", () => {
    mockRoutes(undefined, true);
    setup();
    expect(screen.getByText("loading")).toBeInTheDocument();
  });

  it("exposes a route_code -> name Map sized to the agency's route list", () => {
    mockRoutes([
      { route_id: "T50線(39061)", route_short_name: "T50", route_long_name: "石江・新城線", route_code: "39061", trip_headsigns: [] },
      { route_id: "国道・古川線(1021)", route_short_name: "国道", route_long_name: "国道・古川線", route_code: "1021", trip_headsigns: [] },
    ]);
    setup();
    expect(screen.getByTestId("probe")).toHaveTextContent("T50 石江・新城線|2");
  });
});
