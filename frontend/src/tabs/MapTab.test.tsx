import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useNavigate } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import i18n from "../i18n";
import * as client from "../api/client";
import * as hooks from "../api/hooks";
import * as useRouteNamesModule from "../api/useRouteNames";
import * as mapStyle from "../styles/mapStyle";
import { MapTab } from "./MapTab";
import { stubReducedMotion } from "../test/reducedMotion";
import { decl, ruleBody } from "../test/cssRules";
import { formatDateTime, formatReportTime } from "../utils/format";
import type { LiveTrip, LiveTripsResponse, RouteSummaryResponse, ServiceHoursResponse } from "../api/types";
import { MockMap } from "../test/maplibreMock";

vi.mock("maplibre-gl", () => import("../test/maplibreMock"));

function liveTrips(rows: LiveTripsResponse["rows"] = [], truncated = false): LiveTripsResponse {
  return { latest_captured_at: rows.length ? "2026-06-01T00:00:00Z" : null, rows, truncated };
}

function todaySummary(): RouteSummaryResponse {
  return { latest_captured_at: null, date: null, routes: [], raw_samples: 0, clamp_count: 0 };
}

function mockCommonHooks() {
  vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
    data: todaySummary(),
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  } as never);
  // No timetable hours by default, so the empty map falls back to the fixed
  // overnight break unless a test supplies them.
  vi.spyOn(hooks, "useServiceHours").mockReturnValue({
    data: { date: "2026-01-01", hours: null },
    isLoading: false,
    error: null,
  } as never);
  vi.spyOn(hooks, "useRouteShape").mockReturnValue({ data: undefined } as never);
  vi.spyOn(hooks, "useLiveTripProgress").mockReturnValue({ data: undefined, isLoading: false } as never);
  vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue({
    ...useRouteNamesModule.buildRouteNames([], i18n.t),
    format: (code: string | null | undefined) => code ?? "—",
  });
}

function renderMap(agencyId = "1", search = "") {
  renderWithProviders(
    <MemoryRouter initialEntries={[`/agencies/${agencyId}/map${search}`]}>
      <Routes>
        <Route path="/agencies/:agencyId/map" element={<MapTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("MapTab", () => {
  beforeEach(() => {
    stubReducedMotion();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the operations heading and the empty state when there are no live trips", () => {
    mockCommonHooks();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      dataUpdatedAt: 0,
      data: liveTrips([]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    renderMap();
    expect(screen.getByRole("heading", { name: "Current observations" })).toBeInTheDocument();
    expect(screen.getByText("No live reports received yet")).toBeInTheDocument();
  });

  it("leaves a failed live fetch to the error banner instead of saying no vehicle is reporting", () => {
    mockCommonHooks();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      dataUpdatedAt: 0,
      data: undefined,
      error: new client.ApiError(500, ""),
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    renderMap();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(document.querySelector(".ops-map__empty")).toBeNull();
  });

  it("does not show the empty state and counts observed trips once live rows arrive", () => {
    mockCommonHooks();
    const capturedAt = new Date().toISOString();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      dataUpdatedAt: 0,
      data: liveTrips([
        {
          trip_id: "t1",
          route_code: "R1",
          service_type: null,
          scheduled_time: "08:00:00",
          dep_delay: 30,
          captured_at: capturedAt,
          stop_id: "s1",
          stop_sequence: 1,
          stop_name: "Stop 1",
          stop_lat: 40.8,
          stop_lon: 140.7,
          headsign: "Downtown",
        },
      ]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    renderMap();
    expect(screen.queryByText("No vehicles are reporting right now")).not.toBeInTheDocument();
    expect(screen.getByText("1", { exact: true })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download CSV/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Fit all trips in view/ })).toBeInTheDocument();
    expect(screen.queryByTestId("live-truncated")).not.toBeInTheDocument();
  });

  it("says the counts cover only the trips shown when more were reporting, counting what the tiles count", () => {
    mockCommonHooks();
    const capturedAt = new Date().toISOString();
    const row: LiveTrip = {
      trip_id: "t1",
      route_code: "R1",
      service_type: null,
      scheduled_time: "08:00:00",
      dep_delay: 30,
      captured_at: capturedAt,
      stop_id: "s1",
      stop_sequence: 1,
      stop_name: "Stop 1",
      stop_lat: 40.8,
      stop_lon: 140.7,
      headsign: "Downtown",
    };
    // An hour-old report is dropped from the tiles, so the notice must not count it either.
    const stale = { ...row, trip_id: "t2", captured_at: new Date(Date.now() - 60 * 60_000).toISOString() };
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      dataUpdatedAt: 0,
      data: liveTrips([row, stale], true),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    renderMap();
    expect(screen.getByTestId("live-truncated")).toHaveTextContent(
      "Too many trips to show at once: showing 1, and the counts cover only these",
    );
  });
});

describe("MapTab day playback scope", () => {
  beforeEach(() => {
    stubReducedMotion();
    mockCommonHooks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("scopes the day's frames to the routes the map is filtered to", () => {
    const timeline = vi.spyOn(hooks, "useTimeline").mockReturnValue({ data: undefined, isLoading: true } as never);
    renderMap("1", "?routes=R1,R2");
    expect(timeline).toHaveBeenLastCalledWith(1, false, ["R1", "R2"]);
  });
});

describe("MapTab when no trip is reporting", () => {
  beforeEach(() => {
    stubReducedMotion();
    mockCommonHooks();
    vi.spyOn(hooks, "useTimeline").mockReturnValue({ data: undefined, isLoading: true } as never);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  function mockLive(latest: string | null, rows: LiveTrip[] = []) {
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      dataUpdatedAt: 0,
      data: { latest_captured_at: latest, rows },
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
  }

  function mockSummary(latest: string | null, isLoading = false) {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: isLoading ? undefined : { ...todaySummary(), latest_captured_at: latest },
      error: null,
      isLoading,
      isFetching: isLoading,
      refetch: vi.fn(),
    } as never);
  }

  function reported(tripId: string, capturedAt: string, routeCode = "R1"): LiveTrip {
    return {
      trip_id: tripId,
      route_code: routeCode,
      service_type: null,
      scheduled_time: "18:00:00",
      dep_delay: 420,
      captured_at: capturedAt,
      stop_id: `s-${tripId}`,
      stop_sequence: 3,
      stop_name: "Stop",
      stop_lat: 34.39,
      stop_lon: 132.46,
      headsign: "Downtown",
    };
  }

  function emptyOverlay(): HTMLElement | null {
    return document.querySelector<HTMLElement>(".ops-map__empty");
  }

  function tileValue(label: string | RegExp): string | null {
    return screen.getByText(label).closest(".stat-tile")?.querySelector(".stat-tile__value")?.textContent ?? null;
  }

  // 18:16 JST, eleven minutes after the feed last reported.
  const DAYTIME = "2026-10-25T09:16:49Z";
  const LAST_REPORT = "2026-10-25T09:05:49Z";

  it("says since when no report has arrived in the daytime, instead of calling it night", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(DAYTIME));
    mockLive(LAST_REPORT, [reported("t1", LAST_REPORT)]);
    renderMap();
    const time = formatDateTime(LAST_REPORT, { timeStyle: "short", timeZone: "Asia/Tokyo" });
    const empty = emptyOverlay()!;
    expect(empty).toHaveTextContent(`No new reports since ${time} JST (11 min)`);
    expect(empty).toHaveTextContent("The operator's feed may have stopped.");
    expect(empty).not.toHaveTextContent("late at night");
    expect(empty).not.toHaveTextContent("No vehicles are reporting right now");
  });

  it("reads the counts as unknown, not zero, while the feed is stale", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(DAYTIME));
    mockLive(LAST_REPORT, [reported("t1", LAST_REPORT)]);
    renderMap();
    expect(tileValue("Observed")).toBe("—");
    expect(tileValue(/^5\+\smin late$/)).toBe("—");
    expect(screen.queryByText("On-time rate")).toBeNull();
  });

  it("explains an empty map overnight by the hour, after the evening's last report", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    // 02:30 JST; the last report came at 23:40 JST.
    vi.setSystemTime(new Date("2026-10-25T17:30:00Z"));
    mockLive("2026-10-25T14:40:00Z");
    renderMap();
    const empty = emptyOverlay()!;
    expect(empty).toHaveTextContent("No vehicles are reporting right now");
    expect(empty).toHaveTextContent("late at night");
    expect(empty).not.toHaveTextContent("may have stopped");
    expect(tileValue("Observed")).toBe("0");
  });

  it("names neither the night nor the feed when a fresh feed has nothing on the filtered routes, and counts zero", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    // 14:00 JST; the feed reported a minute ago, on a route the filter leaves out.
    vi.setSystemTime(new Date("2026-10-25T05:00:00Z"));
    mockLive("2026-10-25T04:59:00Z", [reported("t1", "2026-10-25T04:59:00Z", "R1")]);
    renderMap("1", "?routes=R2");
    const empty = emptyOverlay()!;
    expect(empty).toHaveTextContent("No vehicles are reporting right now");
    expect(empty).not.toHaveTextContent("late at night");
    expect(empty).not.toHaveTextContent("may have stopped");
    expect(tileValue("Observed")).toBe("0");
  });

  function mockHours(data: ServiceHoursResponse | undefined, state: { isLoading?: boolean; error?: Error } = {}) {
    return vi.spyOn(hooks, "useServiceHours").mockReturnValue({
      data,
      isLoading: state.isLoading ?? false,
      error: state.error ?? null,
    } as never);
  }

  // Service day 10/25 by the timetable: the first trip leaves at 06:05 JST and
  // the last arrives at 21:48 JST.
  const HOURS_25: ServiceHoursResponse = {
    date: "2026-10-25",
    hours: { first_departure_at: "2026-10-24T21:05:00Z", last_arrival_at: "2026-10-25T12:48:00Z" },
  };
  const jstTime = (iso: string) => `${formatDateTime(iso, { timeStyle: "short", timeZone: "Asia/Tokyo" })} JST`;

  it("says the day's service has ended after the last trip, instead of blaming the feed", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-25T13:30:00Z")); // 22:30 JST
    mockLive("2026-10-25T12:40:00Z"); // 21:40 JST
    mockHours(HOURS_25);
    renderMap();
    const empty = emptyOverlay()!;
    expect(empty).toHaveTextContent(`Today's service has ended (last trip arrived ${jstTime(HOURS_25.hours!.last_arrival_at)})`);
    expect(empty).toHaveTextContent("The timetable has no trips until the next first departure.");
    expect(empty).not.toHaveTextContent("may have stopped");
    expect(within(empty).queryByRole("button", { name: "Show last reported positions" })).toBeNull();
    expect(tileValue("Observed")).toBe("0");
  });

  it("names the first trip's time before it leaves, after the fixed night is over", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-24T20:30:00Z")); // 05:30 JST on the 25th
    mockLive("2026-10-24T12:40:00Z"); // 21:40 JST the evening before
    mockHours(HOURS_25);
    renderMap();
    const empty = emptyOverlay()!;
    expect(empty).toHaveTextContent(`The first trip leaves at ${jstTime(HOURS_25.hours!.first_departure_at)}`);
    expect(empty).toHaveTextContent("The timetable has no trips before the first departure.");
    expect(empty).not.toHaveTextContent("may have stopped");
    expect(tileValue("Observed")).toBe("0");
  });

  it("still says the feed may have stopped when it goes quiet during service hours", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(DAYTIME));
    mockLive(LAST_REPORT);
    mockHours(HOURS_25);
    renderMap();
    expect(emptyOverlay()).toHaveTextContent("The operator's feed may have stopped.");
    expect(tileValue("Observed")).toBe("—");
  });

  it("asks for the hours of the service day the clock is in, which turns over at 04:00 JST", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-25T17:30:00Z")); // 02:30 JST on the 26th
    mockLive("2026-10-25T14:40:00Z");
    const hoursHook = mockHours(HOURS_25);
    renderMap();
    expect(hoursHook).toHaveBeenCalledWith(1, "2026-10-25");
  });

  it("decides nothing about an empty map while the hours that explain it are still loading", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(DAYTIME));
    mockLive(LAST_REPORT);
    mockHours(undefined, { isLoading: true });
    renderMap();
    expect(emptyOverlay()).toBeNull();
  });

  it("falls back to the fixed night when the hours cannot be read", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-25T17:30:00Z")); // 02:30 JST
    mockLive("2026-10-25T14:40:00Z");
    mockHours(undefined, { error: new Error("unavailable") });
    renderMap();
    expect(emptyOverlay()).toHaveTextContent("late at night");
  });

  it("puts the end of service and the first trip in Japanese with their times", async () => {
    await i18n.changeLanguage("ja");
    try {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date("2026-10-25T13:30:00Z")); // 22:30 JST
      mockLive("2026-10-25T12:40:00Z");
      mockHours(HOURS_25);
      renderMap();
      expect(emptyOverlay()).toHaveTextContent("本日の運行は終了しました（最終便の到着 21:48 JST）"); // i18n-ignore: asserts the ja copy
      cleanup();
      vi.setSystemTime(new Date("2026-10-24T20:30:00Z")); // 05:30 JST
      mockLive("2026-10-24T12:40:00Z");
      renderMap();
      expect(emptyOverlay()).toHaveTextContent("始発は 6:05 JST です"); // i18n-ignore: asserts the ja copy
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("says nothing has been received when neither the live feed nor the history has a report", () => {
    mockLive(null);
    renderMap();
    expect(emptyOverlay()).toHaveTextContent("No live reports received yet");
    expect(emptyOverlay()).not.toHaveTextContent("late at night");
    expect(tileValue("Observed")).toBe("—");
  });

  it("dates a feed whose live rows have aged out by the summary's newest report, in the header and on the map", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(DAYTIME));
    mockLive(null);
    mockSummary("2026-10-22T12:59:00Z");
    renderMap();
    const time = formatReportTime("2026-10-22T12:59:00Z");
    expect(screen.getByText(`Feed quiet for 2 days · last report ${time}`)).toBeInTheDocument();
    expect(emptyOverlay()).toHaveTextContent(`No new reports since ${time} (2 days)`);
    expect(tileValue("Observed")).toBe("—");
  });

  it("decides nothing about an empty map while the summary that dates it is still loading", () => {
    mockLive(null);
    mockSummary(null, true);
    renderMap();
    expect(emptyOverlay()).toBeNull();
  });

  it("offers the last reported positions of a stalled feed, labelled as not current", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(DAYTIME));
    const addSource = vi.spyOn(MockMap.prototype, "addSource");
    const liveSourceFeatures = () => {
      const calls = addSource.mock.calls as unknown as [string, { data?: GeoJSON.FeatureCollection }][];
      return calls.filter(([id]) => id === "live-trips").at(-1)?.[1].data?.features.length ?? 0;
    };
    mockLive(LAST_REPORT, [reported("t1", LAST_REPORT), reported("t2", LAST_REPORT)]);
    renderMap();
    expect(liveSourceFeatures()).toBe(0);

    fireEvent.click(within(emptyOverlay()!).getByRole("button", { name: "Show last reported positions" }));
    const time = formatDateTime(LAST_REPORT, { timeStyle: "short", timeZone: "Asia/Tokyo" });
    expect(emptyOverlay()).toBeNull();
    expect(screen.getByText(`Last reported positions as of ${time} JST, not where vehicles are now`)).toBeInTheDocument();
    expect(liveSourceFeatures()).toBe(2);
    // The snapshot is drawn, not counted: the tiles still describe now.
    expect(tileValue("Observed")).toBe("—");

    fireEvent.click(screen.getByRole("button", { name: "Hide last reported positions" }));
    expect(emptyOverlay()).toHaveTextContent(`No new reports since ${time} JST`);
    expect(liveSourceFeatures()).toBe(0);
  });

  it("offers no last positions when the feed left none to show", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(DAYTIME));
    mockLive(null);
    mockSummary(LAST_REPORT);
    renderMap();
    expect(within(emptyOverlay()!).queryByRole("button", { name: "Show last reported positions" })).toBeNull();
  });

  it("says how long the feed has been quiet and when it last reported", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-03T13:24:00Z"));
    mockLive("2026-10-03T12:41:00Z");
    renderMap();
    const time = formatDateTime("2026-10-03T12:41:00Z", { timeStyle: "short", timeZone: "Asia/Tokyo" });
    const status = screen.getByText(`Feed quiet for 43 min · last report ${time} JST`);
    expect(status.closest(".ops-freshness")).toHaveClass("ops-freshness--stale");
  });

  it("reads a report a few seconds ahead of a lagging client clock as just updated, not unknown", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-03T13:24:00Z"));
    mockLive("2026-10-03T13:24:20Z");
    renderMap();
    const status = screen.getByText("Last updated just now");
    expect(status.closest(".ops-freshness")).toHaveClass("ops-freshness--normal");
  });

  it("still calls a report far ahead of the client clock unknown", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-03T13:24:00Z"));
    mockLive("2026-10-03T13:30:00Z");
    renderMap();
    expect(screen.getByText("Last updated —").closest(".ops-freshness")).toHaveClass("ops-freshness--unknown");
  });

  it("counts a feed quiet since an earlier day in days, and dates its last report", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-03T13:24:00Z"));
    mockLive("2026-09-29T00:59:00Z");
    renderMap();
    expect(screen.getByText(`Feed quiet for 4 days · last report ${formatDateTime("2026-09-29T00:59:00Z", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Tokyo" })} JST`)).toBeInTheDocument();
  });

  it("marks a quiet feed in amber, not alarm red", () => {
    const css = readFileSync(resolve(process.cwd(), "src/tabs/map/operationsMap.css"), "utf8");
    expect(decl(ruleBody(css, ".ops-freshness--stale > span"), "background")).toBe("var(--color-warning)");
  });

  it("offers the day's replay on an empty map, which then has the map to itself", async () => {
    mockLive(null);
    renderMap();
    const empty = emptyOverlay()!;
    await userEvent.click(within(empty).getByRole("button", { name: "Play the day" }));
    expect(emptyOverlay()).toBeNull();
    expect(screen.getByRole("button", { name: "Back to current observations" })).toHaveAttribute("aria-pressed", "true");
  });

  it("offers no export and no framing while there is nothing to export or frame", () => {
    mockLive(null);
    renderMap();
    expect(screen.queryByRole("button", { name: /Download CSV/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Fit all trips in view/ })).toBeNull();
  });

  it("claims no absence of delays while nothing is observed", () => {
    mockLive(null);
    renderMap();
    expect(screen.queryByText("No delays of 5+ minutes observed for these filters")).toBeNull();
  });
});

describe("MapTab basemap style from the URL", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    // Two cases persist a preference; later cases must start from none.
    localStorage.clear();
  });

  function renderWithStyle(search: string) {
    mockCommonHooks();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      dataUpdatedAt: 0,
      data: liveTrips([]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    renderMap("1", search);
    const entry = screen.getByRole("button", { name: "Map style" });
    return entry.querySelector("img")?.getAttribute("src") ?? "";
  }

  it("honours a style id the catalog defines", () => {
    expect(renderWithStyle("?style=photo")).toContain("/seamlessphoto/");
  });

  it("falls back to the default basemap for a style id the catalog does not define", () => {
    const src = renderWithStyle("?style=garbage");
    expect(src).toContain("/pale/");
    expect(src).not.toContain("tile.openstreetmap.org");
  });

  it("paints the first frame from the URL's style, not the persisted preference", () => {
    mapStyle.writeMapStylePref("osm");
    const build = vi.spyOn(mapStyle, "buildStyle");
    renderWithStyle("?style=photo");
    expect(build).toHaveBeenCalled();
    expect(build.mock.calls[0]?.[0]).toBe("photo");
  });

  it("falls back to the persisted preference when the URL names no style", () => {
    mapStyle.writeMapStylePref("osm");
    const build = vi.spyOn(mapStyle, "buildStyle");
    renderWithStyle("");
    expect(build.mock.calls[0]?.[0]).toBe("osm");
  });
});

describe("MapTab delayed-trips cap", () => {
  beforeEach(() => {
    stubReducedMotion();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function delayedTrip(route: string, index: number, capturedAt: string): LiveTrip {
    return {
      trip_id: `${route}-${index}`,
      route_code: route,
      service_type: null,
      scheduled_time: "08:00:00",
      dep_delay: 600 + index,
      captured_at: capturedAt,
      stop_id: `s${index}`,
      stop_sequence: 1,
      stop_name: `Stop ${index}`,
      stop_lat: 40.8,
      stop_lon: 140.7,
      headsign: "Downtown",
    };
  }

  /** Stands in for the filter dock: a route-filter change is a URL change. */
  function FilterRoutes() {
    const navigate = useNavigate();
    return <button type="button" onClick={() => navigate("/agencies/1/map?routes=R1,R3")}>refilter</button>;
  }

  it("keeps a raised cap across a live refetch and resets it when the route filter changes", () => {
    mockCommonHooks();
    vi.spyOn(hooks, "useRouteStopProfile").mockReturnValue({ data: undefined } as never);
    const capturedAt = new Date().toISOString();
    // Either two-route filter holds just past the 200-row cap. Two routes
    // rather than one keeps any single route from being focused, which would
    // render its every trip a second time in the trip panel.
    const rows = [
      ...Array.from({ length: 201 }, (_, i) => delayedTrip("R1", i, capturedAt)),
      delayedTrip("R2", 0, capturedAt),
      delayedTrip("R3", 0, capturedAt),
    ];
    let liveResult = { data: liveTrips(rows), dataUpdatedAt: 1 };
    vi.spyOn(hooks, "useLiveTrips").mockImplementation(() => ({
      ...liveResult,
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    }) as never);
    // A fresh element per call: re-rendering with the same element object
    // lets React bail out before MapTab ever sees the refetched result.
    const tree = () => (
      <MemoryRouter initialEntries={["/agencies/1/map?routes=R1,R2"]}>
        <FilterRoutes />
        <Routes>
          <Route path="/agencies/:agencyId/map" element={<MapTab />} />
        </Routes>
      </MemoryRouter>
    );
    const { rerender } = renderWithProviders(tree());

    // Text queries, not role queries: resolving accessible names walks every
    // rendered row on each call.
    const remainder = "Show 2 more";
    fireEvent.click(screen.getByText(remainder));
    expect(screen.queryByText(remainder)).not.toBeInTheDocument();

    liveResult = { data: liveTrips(rows), dataUpdatedAt: 2 };
    rerender(tree());
    expect(screen.queryByText(remainder)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("refilter"));
    expect(screen.getByText(remainder)).toBeInTheDocument();
  });

  it("keeps a poll's fresh rows when no tick has moved the clock since mount", () => {
    // Only Date is faked: nothing advances the page's own tick here, which is
    // exactly the quiet-feed case where every poll re-arms it before it fires.
    vi.useFakeTimers({ toFake: ["Date"] });
    const mounted = Date.parse("2026-06-01T00:00:00Z");
    vi.setSystemTime(mounted);
    mockCommonHooks();
    vi.spyOn(hooks, "useRouteStopProfile").mockReturnValue({ data: undefined } as never);
    const poll = (at: number) => {
      const capturedAt = new Date(at).toISOString();
      return { data: { latest_captured_at: capturedAt, rows: [delayedTrip("R1", 0, capturedAt)] }, dataUpdatedAt: at };
    };
    let liveResult = poll(mounted);
    vi.spyOn(hooks, "useLiveTrips").mockImplementation(() => ({
      ...liveResult,
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    }) as never);
    const tree = () => (
      <MemoryRouter initialEntries={["/agencies/1/map"]}>
        <Routes>
          <Route path="/agencies/:agencyId/map" element={<MapTab />} />
        </Routes>
      </MemoryRouter>
    );
    const { rerender } = renderWithProviders(tree());
    expect(screen.getAllByText(/Stop 0/).length).toBeGreaterThan(0);

    const later = mounted + 120_000;
    vi.setSystemTime(later);
    liveResult = poll(later);
    rerender(tree());
    expect(screen.getAllByText(/Stop 0/).length).toBeGreaterThan(0);
    expect(screen.queryByText("Last updated —")).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it.each([
    [401, "Sign in to fetch the latest live observation"],
    [429, "Too many refreshes; please try again shortly"],
    [500, "Reload failed"],
  ])("explains a refused refresh (%i) without inviting a retry it cannot win", async (status, message) => {
    mockCommonHooks();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      dataUpdatedAt: 0,
      data: liveTrips([]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(client, "apiPost").mockRejectedValue(new client.ApiError(status, ""));
    renderMap();
    await userEvent.click(screen.getByRole("button", { name: "Fetch the latest live observation" }));
    expect(await screen.findByText(message)).toBeInTheDocument();
  });
});

describe("MapTab manual refresh", () => {
  beforeEach(() => {
    stubReducedMotion();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("dates the refreshed observation against the clock at the moment the refresh lands", async () => {
    // Only Date is faked: the refresh's promises and React's scheduler keep
    // running on real timers.
    vi.useFakeTimers({ toFake: ["Date"] });
    const mounted = Date.parse("2026-06-01T00:00:00Z");
    vi.setSystemTime(mounted);
    mockCommonHooks();
    // Between render ticks a fresh observation can be newer than the last
    // tick's `now`; timing it against that stale tick would read as future.
    const fresh = new Date(mounted + 25_000).toISOString();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      dataUpdatedAt: 0,
      data: liveTrips([]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn().mockResolvedValue({ isError: false, data: { latest_captured_at: fresh, rows: [] } }),
    } as never);
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: todaySummary(),
      isLoading: false,
      isFetching: false,
      refetch: vi.fn().mockResolvedValue({ isError: false }),
    } as never);
    vi.spyOn(client, "apiPost").mockResolvedValue({ status: "ok", inserted: 3 } as never);
    renderMap();
    vi.setSystemTime(mounted + 30_000);
    fireEvent.click(screen.getByRole("button", { name: "Fetch the latest live observation" }));
    expect(await screen.findByText("Live data loaded: 3 rows (just now)")).toBeInTheDocument();
  });
});

describe("MapTab relief layer", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  /** Reduced motion always (so camera moves are instant cuts); `phone`
   *  additionally matches the mobile breakpoint query. */
  function stubViewport(phone: boolean) {
    vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
      matches: query.includes("prefers-reduced-motion") || (phone && query.includes("max-width")),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList);
  }

  function renderAndOpenPanel(search = "") {
    mockCommonHooks();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      dataUpdatedAt: 0,
      data: liveTrips([]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    renderMap("1", search);
    fireEvent.click(screen.getByRole("button", { name: "Map style" }));
    return screen.getByRole("button", { name: /Relief/ });
  }

  function pitches(easeTo: { mock: { calls: unknown[][] } }) {
    return easeTo.mock.calls.map(([options]) => (options as { pitch?: number }).pitch);
  }

  it("starts off at every width and never tilts the map unasked", () => {
    for (const phone of [false, true]) {
      stubViewport(phone);
      const easeTo = vi.spyOn(MockMap.prototype, "easeTo");
      const chip = renderAndOpenPanel();
      expect(chip).toHaveAttribute("aria-pressed", "false");
      expect(easeTo).not.toHaveBeenCalled();
      cleanup();
      vi.restoreAllMocks();
    }
  });

  it("switching on tilts through the camera module and persists; switching off lays the map flat", () => {
    stubViewport(false);
    const easeTo = vi.spyOn(MockMap.prototype, "easeTo");
    fireEvent.click(renderAndOpenPanel());
    expect(screen.getByRole("button", { name: /Relief/ })).toHaveAttribute("aria-pressed", "true");
    expect(localStorage.getItem("transit.mapRelief")).toBe("1");
    expect(pitches(easeTo)).toEqual([35]);

    fireEvent.click(screen.getByRole("button", { name: /Relief/ }));
    expect(localStorage.getItem("transit.mapRelief")).toBe("0");
    expect(pitches(easeTo)).toEqual([35, 0]);
  });

  it("lets the URL override the stored preference", () => {
    stubViewport(false);
    localStorage.setItem("transit.mapRelief", "0");
    expect(renderAndOpenPanel("?relief=1")).toHaveAttribute("aria-pressed", "true");
  });
});

describe("MapTab segment highlight and ambient light chips", () => {
  beforeEach(() => {
    localStorage.clear();
    stubReducedMotion();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  function openPanel() {
    mockCommonHooks();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      dataUpdatedAt: 0,
      data: liveTrips([]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    renderMap("1");
    fireEvent.click(screen.getByRole("button", { name: "Map style" }));
  }

  it("both start on, and each toggle persists under its own key", () => {
    openPanel();
    const pearl = screen.getByRole("button", { name: /Segment highlight/ });
    const light = screen.getByRole("button", { name: /Ambient light/ });
    expect(pearl).toHaveAttribute("aria-pressed", "true");
    expect(light).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(pearl);
    expect(screen.getByRole("button", { name: /Segment highlight/ })).toHaveAttribute("aria-pressed", "false");
    expect(localStorage.getItem("transit.mapPearl")).toBe("0");
    expect(localStorage.getItem("transit.mapLight")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Ambient light/ }));
    expect(screen.getByRole("button", { name: /Ambient light/ })).toHaveAttribute("aria-pressed", "false");
    expect(localStorage.getItem("transit.mapLight")).toBe("0");
  });

  it("restores a stored off preference", () => {
    localStorage.setItem("transit.mapPearl", "0");
    localStorage.setItem("transit.mapLight", "0");
    openPanel();
    expect(screen.getByRole("button", { name: /Segment highlight/ })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: /Ambient light/ })).toHaveAttribute("aria-pressed", "false");
  });
});
