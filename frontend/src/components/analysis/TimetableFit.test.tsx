import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UseQueryResult } from "@tanstack/react-query";
import { renderWithProviders } from "../../test/renderWithProviders";
import i18n from "../../i18n";
import * as hooks from "../../api/hooks";
import type { Scope } from "../../api/scope";
import * as timetableFitApi from "./timetableFitApi";
import type { TimetableFitResponse, TimetableFitSegment } from "./timetableFitApi";
import { TimetableFit } from "./TimetableFit";

// A full-tier agency, whose not-shown reason is the feed's own.
vi.mock("../../api/useCapabilityTier", () => ({ useCapabilityTier: () => "full" }));

const SCOPE: Scope = {
  from: "2026-04-01",
  to: "2026-04-28",
  dow: "all",
  time_band: "all",
  service: "all",
  routes: ["R1"],
  hour: null,
  stop: null,
  dir: null,
  late: null,
  early: null,
};

function segment(over: Partial<TimetableFitSegment>): TimetableFitSegment {
  return {
    from_stop_id: "S1",
    from_stop_name: "Kamiyacho",
    to_stop_id: "S2",
    to_stop_name: "Hatchobori",
    time_band: "morning",
    to_stop_sequence: 2,
    samples: 20,
    run_p50_sec: 100,
    run_p85_sec: 130,
    scheduled_run_sec: 120,
    padding_sec: 20,
    hold_eligible: 10,
    hold_rate: 0.25,
    ...over,
  };
}

const RESPONSE: TimetableFitResponse = {
  available: true,
  unavailable_reason: null,
  last_checked: null,
  value_basis: "feed_prediction",
  static_version_id: "gtfs_static_20260401",
  days: 20,
  terminus_arrivals: 40,
  terminus_early_rate: 0.1,
  segments: [
    segment({}),
    segment({ time_band: "evening", samples: 5, run_p50_sec: 150, padding_sec: -30 }),
    segment({ from_stop_id: "S2", from_stop_name: "Hatchobori", to_stop_id: "S3", to_stop_name: null, to_stop_sequence: 3 }),
  ],
  definition: {
    preset: null,
    early_tolerance_sec: null,
    late_tolerance_sec: 60,
    early_departure_sec: null,
    exclusion_threshold_sec: 7200,
    measurement_point: "all_stops_all_observations",
    dedup_rule: "latest_observation_per_stop_event",
    method_version: "2026.11.0",
    source: "aggregates",
    built_at: null,
    value_basis: "feed_prediction",
    timetable_versions: null,
    source_mix: null,
  },
  scope_applied: {},
};

function stub(data: TimetableFitResponse | undefined, error: Error | null = null) {
  return vi.spyOn(timetableFitApi, "useTimetableFit").mockReturnValue({
    data,
    error,
    refetch: vi.fn(),
  } as unknown as UseQueryResult<TimetableFitResponse>);
}

function open(scope: Scope = SCOPE) {
  renderWithProviders(<TimetableFit agencyId={9} ctx={scope} route="R1" />);
}

describe("TimetableFit", () => {
  beforeEach(async () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [], isPending: false } as never);
    await i18n.changeLanguage("en");
  });
  afterEach(() => vi.restoreAllMocks());

  it("starts on the band with the most runs, its segments in the route's order, as feed-based estimates", () => {
    const hook = stub(RESPONSE);
    open();
    expect(hook).toHaveBeenCalledWith(9, SCOPE, "R1");
    expect(screen.getByText(/estimates from the delays the operator's feed predicted/)).toBeInTheDocument();
    expect(screen.getByText(/\(gtfs_static_20260401\); days read: 20\./)).toBeInTheDocument();
    expect(screen.getByLabelText("Departure time band")).toHaveValue("morning");
    const table = screen.getByRole("table", { name: "Running time per segment, Morning (05–09)" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("Kamiyacho → Hatchobori");
    expect(rows[1]).toHaveTextContent("+20");
    expect(rows[1]).toHaveTextContent("25.0%");
    expect(rows[2]).toHaveTextContent("Hatchobori → S3");
    expect(screen.getByText("At the last stop, 10.0% of 40 arrivals were 60 s or more ahead of the timetable.")).toBeInTheDocument();
  });

  it("shows another band's segments once it is picked", async () => {
    stub(RESPONSE);
    open();
    await userEvent.selectOptions(screen.getByLabelText("Departure time band"), "evening");
    const rows = within(screen.getByRole("table", { name: /Evening/ })).getAllByRole("row");
    expect(rows).toHaveLength(2);
    expect(rows[1]).toHaveTextContent("150");
    expect(rows[1]).toHaveTextContent("-30");
  });

  it("names a lone band rather than offering a choice of one", () => {
    stub({ ...RESPONSE, segments: [segment({})] });
    open();
    expect(screen.queryByLabelText("Departure time band")).not.toBeInTheDocument();
    expect(screen.getByText("Departure time band: Morning (05–09)")).toBeInTheDocument();
  });

  it("counts the segments with no scheduled departure time apart, and says when none is timed", () => {
    stub({ ...RESPONSE, static_version_id: null, terminus_early_rate: null, segments: [segment({ time_band: null })] });
    open();
    expect(screen.getByText("No segment of this route was observed in this period.")).toBeInTheDocument();
    expect(screen.getByText("Segments without a scheduled departure time, not shown: 1")).toBeInTheDocument();
    expect(screen.getByText(/days read: 20\./)).not.toHaveTextContent("(");
    expect(screen.queryByText(/At the last stop/)).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says the hour filter does not narrow it", () => {
    stub(RESPONSE);
    open({ ...SCOPE, hour: [7, 9] });
    expect(screen.getByText("The hour filter does not apply here: segments are read by time band.")).toBeInTheDocument();
  });

  it("says why a feed that sends no arrivals shows nothing", () => {
    stub({ ...RESPONSE, available: false, unavailable_reason: "feed_unsupported", segments: [] });
    open();
    expect(screen.getByText(/can't be taken from this agency's feed/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("offers a retry when the request fails", () => {
    stub(undefined, new Error("boom"));
    open();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
