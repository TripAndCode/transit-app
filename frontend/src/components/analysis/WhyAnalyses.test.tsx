import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, screen, within } from "@testing-library/react";
import i18n from "../../i18n";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import * as hooks from "../../api/hooks";
import * as config from "../../api/config";
import * as isAdmin from "../../api/useIsAdmin";
import type { DwellRunPayload, HeadwayQualityResponse, WeatherDelayResponse } from "../../api/types";
import { WhyAnalyses } from "./WhyAnalyses";

const CTX = { from: "2026-09-01", to: "2026-09-07", dow: "all", time_band: "all" };

const EXPIRED = "probe_expired" as const;
const EXPIRED_TEXT =
  "The check of this feed's arrival times and related data has expired, so this isn't shown until it is re-checked (last checked Sep 11, 2026).";

const NO_STATION: WeatherDelayResponse = {
  available: false,
  station: null,
  wet_day_threshold_mm: 1,
  wet: { days: 0, samples: 0, avg_delay_sec: null, avg_precip_mm: null },
  dry: { days: 0, samples: 0, avg_delay_sec: null, avg_precip_mm: null },
  delta_sec: null,
  low_confidence: false,
  ctx: CTX,
  disclaimer: "d",
  attribution: "a",
};

const WEATHER: WeatherDelayResponse = {
  ...NO_STATION,
  available: true,
  station: { station_id: "47765", station_name: "Example Station", note: null },
  wet: { days: 6, samples: 900, avg_delay_sec: 80, avg_precip_mm: 7 },
  dry: { days: 20, samples: 3000, avg_delay_sec: 60, avg_precip_mm: 0 },
  delta_sec: 20,
};

const HEADWAY: HeadwayQualityResponse = {
  rows: [{ route_code: "100", ewt_sec: 60, cov: 0.4, long_gap_rate: 0.1, samples: 40 }],
  ctx: CTX,
};

const DWELL: DwellRunPayload = {
  available: true,
  time_band_supported: true,
  unavailable_reason: null,
  last_checked: null,
  routes: [
    {
      route_code: "7",
      service_type: "all",
      dwell_samples: 12,
      dwell_avg_sec: 30,
      dwell_p50_sec: 25,
      dwell_p90_sec: 60,
      run_samples: 10,
      run_avg_sec: 120,
      run_p50_sec: 110,
      run_p90_sec: 200,
    },
  ],
};

const BASIC_REASON =
  "This agency's feed carries basic data only, which covers on-time performance. This needs timetable-matched records its feed doesn't provide.";

function unavailableDwell(reason: DwellRunPayload["unavailable_reason"], lastChecked: string | null = null): DwellRunPayload {
  return { available: false, time_band_supported: true, routes: [], unavailable_reason: reason, last_checked: lastChecked };
}

function show(
  payload: DwellRunPayload,
  weather: WeatherDelayResponse,
  headway: HeadwayQualityResponse,
  {
    admin = true,
    weatherOn = true,
    showStatistics = false,
  }: { admin?: boolean; weatherOn?: boolean; showStatistics?: boolean } = {},
) {
  vi.spyOn(config, "useFeatureOn").mockReturnValue(weatherOn);
  vi.spyOn(isAdmin, "useIsAdmin").mockReturnValue(admin);
  // The agency catalogue answered without a tier for this agency, unless a test set one.
  if (!vi.isMockFunction(hooks.useAgencies)) vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [], isPending: false } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(hooks, "useWeatherDelay").mockReturnValue({ data: weather, isLoading: false, error: null, refetch: vi.fn() } as never);
  vi.spyOn(hooks, "useHeadwayQuality").mockReturnValue({ data: headway, isLoading: false, error: null, refetch: vi.fn() } as never);
  const router = createMemoryRouter(
    [{ path: "/agencies/:agencyId/why", element: <WhyAnalyses aid={1} payload={payload} showStatistics={showStatistics} /> }],
    { initialEntries: ["/agencies/1/why?from=2026-09-01&to=2026-09-07"] },
  );
  renderWithProviders(<RouterProvider router={router} />);
  return { router };
}

function notShown() {
  return screen.getByRole("region", { name: "Analyses not shown" });
}

function row(name: string) {
  return within(notShown()).getByRole("listitem", { name });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("WhyAnalyses", () => {
  it("marks a thinly observed average with few data while the counts are hidden, and a solid one not", () => {
    const thick = { ...DWELL.routes[0], route_code: "8", dwell_samples: 400, run_samples: 12 };
    show({ ...DWELL, routes: [DWELL.routes[0], thick] }, WEATHER, HEADWAY);
    const table = screen.getByRole("columnheader", { name: i18n.t("reports.dwell_run.col.dwell_avg") }).closest("table")!;
    const [thin, solid] = within(table).getAllByRole("row").slice(1);
    const cells = (row: HTMLElement) => within(row).getAllByRole("cell");
    // Columns: #, route, dwell avg, running avg.
    expect(within(cells(thin)[2]).getByText("few data")).toHaveClass("caveat-badge");
    expect(within(cells(thin)[3]).getByText("few data")).toBeInTheDocument();
    expect(within(cells(solid)[2]).queryByText("few data")).toBeNull();
    expect(within(cells(solid)[3]).getByText("few data")).toBeInTheDocument();
  });

  it("offers the statistics toggle only where a table it changes is drawn", () => {
    show(DWELL, WEATHER, HEADWAY);
    expect(screen.getByRole("checkbox", { name: "Show statistics" })).toBeInTheDocument();
    cleanup();
    show(unavailableDwell("feed_unsupported"), NO_STATION, { ...HEADWAY, rows: [] }, { weatherOn: false });
    expect(screen.queryByRole("checkbox", { name: "Show statistics" })).toBeNull();
  });

  it("shows the dwell and running averages, and their statistics only on request", () => {
    show(DWELL, WEATHER, HEADWAY);
    const headers = () => screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers()).toContain(i18n.t("reports.dwell_run.col.dwell_avg"));
    expect(headers()).not.toContain(i18n.t("reports.dwell_run.col.dwell_p90"));
    expect(headers()).not.toContain(i18n.t("reports.dwell_run.col.run_samples"));
    cleanup();
    show(DWELL, WEATHER, HEADWAY, { showStatistics: true });
    expect(headers()).toContain(i18n.t("reports.dwell_run.col.dwell_p90"));
    expect(headers()).toContain(i18n.t("reports.dwell_run.col.run_samples"));
  });

  it("lists what it can't show, each with a status and its true cause, instead of three empty cards", () => {
    show(unavailableDwell(EXPIRED, "2026-09-11"), NO_STATION, { rows: [], ctx: CTX, unavailable_reason: EXPIRED, last_checked: "2026-09-11" });

    expect(within(row("Dwell/running time")).getByText("Needs re-check")).toBeInTheDocument();
    expect(within(row("Dwell/running time")).getByText(EXPIRED_TEXT)).toBeInTheDocument();
    expect(within(row("Rain vs. dry average delay")).getByText("Not set up")).toBeInTheDocument();
    expect(within(row("Rain vs. dry average delay")).getByText("No weather station is set up for this agency.")).toBeInTheDocument();
    expect(within(row("Headway quality")).getByText("Needs re-check")).toBeInTheDocument();
    expect(within(row("Headway quality")).getByText(EXPIRED_TEXT)).toBeInTheDocument();

    expect(screen.queryByText(/departures only/)).not.toBeInTheDocument();
    expect(screen.queryByText(/enough data/)).not.toBeInTheDocument();
    expect(screen.queryByText(/one documented weather station/)).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("still points to where delay can be looked at when the split is missing", async () => {
    const { router } = show(unavailableDwell(EXPIRED, "2026-09-11"), NO_STATION, HEADWAY);
    await userEvent.click(screen.getByRole("button", { name: "See when delay happens" }));
    expect(router.state.location.pathname).toBe("/agencies/1/time");
    expect(new URLSearchParams(router.state.location.search).get("from")).toBe("2026-09-01");
  });

  it("shows what it can first and lists only the rest", () => {
    show(DWELL, NO_STATION, { rows: [], ctx: CTX, unavailable_reason: "no_high_frequency_routes", last_checked: null });

    const table = screen.getByRole("table");
    expect(within(table).getByText("Route 7")).toBeInTheDocument();
    expect(table.compareDocumentPosition(notShown()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(notShown()).queryByRole("listitem", { name: "Dwell/running time" })).not.toBeInTheDocument();
    expect(within(row("Rain vs. dry average delay")).getByText("Not set up")).toBeInTheDocument();
    expect(within(row("Headway quality")).getByText("Not applicable")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "See when delay happens" })).not.toBeInTheDocument();
  });

  it("lists nothing when every analysis has something to show", () => {
    show(DWELL, WEATHER, HEADWAY);
    expect(screen.queryByRole("region", { name: "Analyses not shown" })).not.toBeInTheDocument();
    expect(screen.getByText("Station: Example Station")).toBeInTheDocument();
    expect(screen.getByText("Route 100")).toBeInTheDocument();
  });

  it("tells a feed that can't carry arrival times from a check that never ran", () => {
    show(unavailableDwell("feed_unsupported"), WEATHER, { rows: [], ctx: CTX, unavailable_reason: "never_probed", last_checked: null });
    expect(within(row("Dwell/running time")).getByText("Not supported")).toBeInTheDocument();
    expect(
      within(row("Dwell/running time")).getByText(
        "Arrival times and related records can't be taken from this agency's feed, so this can't be shown.",
      ),
    ).toBeInTheDocument();
    expect(within(row("Headway quality")).getByText("Not checked")).toBeInTheDocument();
    expect(
      within(row("Headway quality")).getByText(
        "Whether this agency's feed sends arrival times and related data hasn't been checked yet, so this isn't shown.",
      ),
    ).toBeInTheDocument();
  });

  it("says when routes matched but none has a dwell or running sample, without blaming the filters", () => {
    show(
      { available: true, time_band_supported: true, routes: [], unavailable_reason: "no_paired_samples", last_checked: null },
      WEATHER,
      HEADWAY,
    );
    expect(within(row("Dwell/running time")).getByText("No observations")).toBeInTheDocument();
    expect(
      within(row("Dwell/running time")).getByText(
        "There are no observations in this period from which dwell or running time can be calculated.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("No matching data")).not.toBeInTheDocument();
    expect(screen.queryByText("Try a different range or filters")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "See when delay happens" })).toBeInTheDocument();
  });

  it("leaves an empty scope to the filters' own empty state", () => {
    show({ available: true, time_band_supported: true, routes: [], unavailable_reason: null, last_checked: null }, WEATHER, HEADWAY);
    expect(screen.getByText("No matching data")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Analyses not shown" })).not.toBeInTheDocument();
  });

  it("states a basic-tier agency's tier instead of a pipeline reason", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [{ agency_id: 1, agency_name: "Aomori", feed_url: "", static_url: null, latest_data_date: null, capability_tier: "basic" }],
    } as never);
    show(unavailableDwell("feed_unsupported"), WEATHER, { rows: [], ctx: CTX, unavailable_reason: "feed_unsupported", last_checked: null });

    for (const name of ["Dwell/running time", "Headway quality"]) {
      expect(within(row(name)).getByText("Basic data")).toBeInTheDocument();
      expect(within(row(name)).getByText(BASIC_REASON)).toBeInTheDocument();
    }
    expect(screen.queryByText("Not supported")).not.toBeInTheDocument();
    expect(screen.queryByText(/can't be taken from this agency's feed/)).not.toBeInTheDocument();
  });

  it("dates a refutation and calls it unsupported", () => {
    show(unavailableDwell("probe_refuted", "2026-09-11"), WEATHER, HEADWAY);
    expect(within(row("Dwell/running time")).getByText("Not supported")).toBeInTheDocument();
    expect(
      within(row("Dwell/running time")).getByText(
        "The last check (Sep 11, 2026) found that this agency's feed doesn't send the fields this needs, so it isn't shown.",
      ),
    ).toBeInTheDocument();
  });

  describe("for a reader who is not an admin", () => {
    const READER = { admin: false };
    const UNAVAILABLE = "This can't be shown for this agency's feed right now.";

    it("words a lapsed or never-run check as one neutral line, with no probe state, date or re-check", () => {
      show(unavailableDwell(EXPIRED, "2026-09-11"), NO_STATION, { rows: [], ctx: CTX, unavailable_reason: "never_probed", last_checked: null }, READER);

      for (const name of ["Dwell/running time", "Headway quality"]) {
        expect(within(row(name)).getByText("Not available")).toBeInTheDocument();
        expect(within(row(name)).getByText(UNAVAILABLE)).toBeInTheDocument();
      }
      expect(screen.queryByText("Needs re-check")).not.toBeInTheDocument();
      expect(screen.queryByText("Not checked")).not.toBeInTheDocument();
      expect(screen.queryByText(/re-checked|checked yet|last checked/)).not.toBeInTheDocument();
    });

    it("keeps the feed fact for an unsupported feed but not the check's date for a refuted one", () => {
      show(unavailableDwell("probe_refuted", "2026-09-11"), WEATHER, HEADWAY, READER);
      expect(within(row("Dwell/running time")).getByText("Not available")).toBeInTheDocument();
      expect(
        within(row("Dwell/running time")).getByText(
          "Arrival times and related records can't be taken from this agency's feed, so this can't be shown.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(/Sep 11, 2026/)).not.toBeInTheDocument();
    });
  });

  describe("when the weather flag is off", () => {
    it("mounts no weather panel and lists no weather gap, even for a payload that has data", () => {
      show(DWELL, WEATHER, HEADWAY, { weatherOn: false });
      expect(screen.queryByText("Station: Example Station")).not.toBeInTheDocument();
      expect(screen.queryByText("Rain vs. dry average delay")).not.toBeInTheDocument();
      expect(vi.mocked(hooks.useWeatherDelay)).toHaveBeenCalledWith(1, expect.anything(), false);
    });

    it("lists no weather gap for a payload that would have been one", () => {
      show(DWELL, NO_STATION, HEADWAY, { weatherOn: false });
      expect(screen.queryByRole("region", { name: "Analyses not shown" })).not.toBeInTheDocument();
    });

    it("mounts the panel when the flag is on", () => {
      show(DWELL, WEATHER, HEADWAY, { weatherOn: true });
      expect(screen.getByText("Station: Example Station")).toBeInTheDocument();
    });
  });
});
