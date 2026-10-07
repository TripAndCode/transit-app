import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi, afterEach } from "vitest";
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import { decl, ruleBody } from "../../test/cssRules";
import * as hooks from "../../api/hooks";
import { DataEndContext, isoDaysAgo } from "../../api/scope";
import type { DefinitionMeta, DelayCertificateRow, ReportResponse } from "../../api/types";
import { DelayCertificateLookup } from "./DelayCertificateLookup";
import { STILL_WORKING_AFTER_MS } from "../StillWorking";

const ROUTE = { route_id: "r1", route_short_name: "W54", route_long_name: null, route_code: "W54", trip_headsigns: [] };

const ROWS: DelayCertificateRow[] = [
  ["Aomori City Bus", "W54", "weekday", "2026-09-02", "14:45:00", "14:50:55", 355],
  ["Aomori City Bus", "W54", "weekday", "2026-09-02", "15:15:00", "15:16:10", 70],
];

const DEFINITION: DefinitionMeta = {
  preset: "legacy_60s",
  early_tolerance_sec: null,
  late_tolerance_sec: 60,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
};

function certificate(rows: DelayCertificateRow[]): ReportResponse {
  return { report_type: "delay_certificate", rendered_at: "2026-09-03T00:00:00Z", text: "", rows, definition: DEFINITION };
}

function mockReport(result: { data?: ReportResponse; error?: Error | null; isPlaceholderData?: boolean }) {
  return vi.spyOn(hooks, "useReport").mockImplementation(
    (_aid, reportType) =>
      (reportType == null
        ? { data: undefined, error: null, isPlaceholderData: false, refetch: vi.fn() }
        : { data: undefined, error: null, isPlaceholderData: false, refetch: vi.fn(), ...result }) as never,
  );
}

function renderLookup() {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [ROUTE], isLoading: false } as never);
  return renderWithProviders(
    <DataEndContext value="2026-09-02">
      <DelayCertificateLookup aid={1} />
    </DataEndContext>,
  );
}

async function chooseRoute() {
  await userEvent.click(screen.getByRole("button", { name: "Route" }));
  await userEvent.click(screen.getByRole("option", { name: /W54/ }));
}

describe("DelayCertificateLookup", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("opens on the latest data day and offers no day after today", () => {
    mockReport({});
    renderLookup();
    const date = screen.getByLabelText("Date");
    expect(date).toHaveValue("2026-09-02");
    expect(date).toHaveAttribute("max", isoDaysAgo(0));
  });

  it("asks for nothing until a route is chosen, then for every late departure of it that day", async () => {
    const useReport = mockReport({ data: certificate(ROWS) });
    renderLookup();
    expect(useReport.mock.calls.every((call) => call[1] == null)).toBe(true);
    await chooseRoute();
    const [aid, reportType, scope, options] = useReport.mock.calls.at(-1)!;
    expect([aid, reportType]).toEqual([1, "delay_certificate"]);
    expect(scope).toMatchObject({ from: "2026-09-02", to: "2026-09-02", routes: ["W54"], dow: "all", time_band: "all", service: "all", hour: null, stop: null, dir: null });
    expect(options).toEqual({ thresholdSec: 0, limit: 500 });
  });

  it("lists each late departure by its timetable time and how late it left", async () => {
    mockReport({ data: certificate(ROWS) });
    renderLookup();
    await chooseRoute();
    const departure = screen.getByLabelText("Scheduled departure");
    expect(within(departure).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose a departure",
      "14:45 (5 min 55 s late)",
      "15:15 (1 min 10 s late)",
    ]);
    expect(screen.getByText("A departure that isn't listed left on time or early, or wasn't in the live feed.")).toBeInTheDocument();
  });

  it("certifies the chosen departure in words and figures, ready to print", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    mockReport({ data: certificate(ROWS) });
    renderLookup();
    await chooseRoute();
    await userEvent.selectOptions(screen.getByLabelText("Scheduled departure"), "14:45 (5 min 55 s late)");
    const card = screen.getByRole("region", { name: "Delay certificate for the chosen departure" });
    expect(card).toHaveTextContent(
      "On Wed, Sep 2, 2026, the 14:45 departure of W54 left its first recorded stop at 14:50:55, 5 min 55 s late.",
    );
    expect(within(card).getByText("Operator").nextElementSibling).toHaveTextContent("Aomori City Bus");
    expect(within(card).getByText("Actual departure").nextElementSibling).toHaveTextContent("14:50:55");
    await userEvent.click(within(card).getByRole("button", { name: "Print / Save as PDF" }));
    expect(print).toHaveBeenCalledOnce();
  });

  it("keeps certifying the chosen departure when a refetch adds an earlier one", async () => {
    let rows = ROWS;
    vi.spyOn(hooks, "useReport").mockImplementation(
      (_aid, reportType) =>
        ({ data: reportType == null ? undefined : certificate(rows), error: null, isPlaceholderData: false, refetch: vi.fn() }) as never,
    );
    const { rerender } = renderLookup();
    await chooseRoute();
    await userEvent.selectOptions(screen.getByLabelText("Scheduled departure"), "15:15 (1 min 10 s late)");
    rows = [["Aomori City Bus", "W54", "weekday", "2026-09-02", "14:15:00", "14:17:00", 120], ...ROWS];
    rerender(
      <DataEndContext value="2026-09-02">
        <DelayCertificateLookup aid={1} />
      </DataEndContext>,
    );
    const card = screen.getByRole("region", { name: "Delay certificate for the chosen departure" });
    expect(within(card).getByText("Scheduled departure").nextElementSibling).toHaveTextContent("15:15");
  });

  it("drops the chosen departure when the day changes", async () => {
    vi.spyOn(hooks, "useReport").mockImplementation(
      (_aid, reportType, scope) =>
        ({
          data: reportType == null ? undefined : certificate(ROWS.map((r) => [r[0], r[1], r[2], scope.from, r[4], r[5], r[6]])),
          error: null,
          isPlaceholderData: false,
          refetch: vi.fn(),
        }) as never,
    );
    renderLookup();
    await chooseRoute();
    await userEvent.selectOptions(screen.getByLabelText("Scheduled departure"), "14:45 (5 min 55 s late)");
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-09-01" } });
    expect(screen.queryByRole("region", { name: "Delay certificate for the chosen departure" })).toBeNull();
  });

  it("says so when the route ran no late departure that day", async () => {
    mockReport({ data: certificate([]) });
    renderLookup();
    await chooseRoute();
    expect(screen.getByText("No departure of this route left late on this day.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Scheduled departure")).toBeNull();
  });

  it("shows another route's departures as still loading, not as this route's", async () => {
    mockReport({ data: certificate(ROWS), isPlaceholderData: true });
    renderLookup();
    await chooseRoute();
    expect(screen.getByText("Looking up late departures…")).toBeInTheDocument();
    expect(screen.queryByLabelText("Scheduled departure")).toBeNull();
  });

  it("says a slow lookup is still working, with nothing narrower to offer for one day", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockReport({});
      renderLookup();
      await chooseRoute();
      act(() => vi.advanceTimersByTime(STILL_WORKING_AFTER_MS));
      expect(screen.getByText("Still working on it…")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Narrow/ })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("offers a retry when the lookup fails", async () => {
    mockReport({ error: new Error("boom") });
    renderLookup();
    await chooseRoute();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("prints the certificate without the controls that found it", () => {
    const print = ruleBody(readFileSync(resolve(__dirname, "./DelayCertificateLookup.css"), "utf8"), "@media print");
    expect(decl(ruleBody(print, ".cert-lookup__controls"), "display")).toBe("none");
    expect(decl(ruleBody(print, ".cert-card__print"), "display")).toBe("none");
  });
});
