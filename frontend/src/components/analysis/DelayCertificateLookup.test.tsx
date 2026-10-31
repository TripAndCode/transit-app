import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useState } from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import { decl, ruleBody } from "../../test/cssRules";
import * as hooks from "../../api/hooks";

import type { DefinitionMeta, DelayCertificateRow, ReportResponse } from "../../api/types";
import i18n from "../../i18n";
import { DelayCertificateLookup } from "./DelayCertificateLookup";
import { STILL_WORKING_AFTER_MS } from "../StillWorking";

const ROUTE = { route_id: "r1", route_short_name: "W54", route_long_name: null, route_code: "W54", trip_headsigns: [] };

const ROWS: DelayCertificateRow[] = [
  ["Aomori City Bus", "W54", "weekday", "2026-09-02", "14:45:00", "14:50:55", 355, "Aomori Station", true],
  ["Aomori City Bus", "W54", "weekday", "2026-09-02", "15:15:00", "15:16:10", 70, "Furukawa", false],
];

const LATEST_DAY = "2026-09-02";

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

/** The lookup as its section holds it: the section keeps the day, so the
 *  staff list below can follow it. */
function Lookup() {
  const [date, setDate] = useState(LATEST_DAY);
  return <DelayCertificateLookup aid={1} date={date} latestDay={LATEST_DAY} onDateChange={setDate} />;
}

function renderLookup() {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [ROUTE], isLoading: false } as never);
  return renderWithProviders(<Lookup />);
}

async function chooseDeparture(option: string) {
  await chooseRoute();
  await userEvent.selectOptions(screen.getByLabelText("Departure"), option);
  return screen.getByRole("region", { name: "Delay record for the chosen departure" });
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

  it("opens on the latest data day and offers no day after it", () => {
    mockReport({});
    renderLookup();
    const date = screen.getByLabelText("Date");
    expect(date).toHaveValue(LATEST_DAY);
    expect(date).toHaveAttribute("max", LATEST_DAY);
  });

  it("keeps a typed day after the data's last day to that last day", () => {
    mockReport({});
    renderLookup();
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-09-05" } });
    expect(screen.getByLabelText("Date")).toHaveValue(LATEST_DAY);
  });

  it("numbers its three steps: the day, the route, then the departure", async () => {
    mockReport({ data: certificate(ROWS) });
    const { container } = renderLookup();
    await chooseRoute();
    const steps = [...container.querySelectorAll(".cert-lookup__step")].map((step) => step.parentElement!.textContent);
    expect(steps.map((text) => text?.slice(0, 1))).toEqual(["1", "2", "3"]);
    expect(steps[2]).toContain("Departure");
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

  it("lists each late departure by its timetable time, the stop it left and how late", async () => {
    mockReport({ data: certificate([...ROWS, ["Aomori City Bus", "W54", "weekday", "2026-09-02", "16:00:00", "16:02:00", 120, null, null]]) });
    renderLookup();
    await chooseRoute();
    const departure = screen.getByLabelText("Departure");
    expect(within(departure).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose a departure",
      "14:45 from Aomori Station (5 min 55 s late)",
      "15:15 from Furukawa (1 min 10 s late)",
      "16:00 (2 min late)",
    ]);
    expect(screen.getByText("A departure that isn't listed left on time or early, or wasn't in the live feed.")).toBeInTheDocument();
  });

  it("records the chosen departure in words and figures, ready to print", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    mockReport({ data: certificate(ROWS) });
    renderLookup();
    const card = await chooseDeparture("14:45 from Aomori Station (5 min 55 s late)");
    expect(card).toHaveTextContent("The 14:45 departure left Aomori Station 5 min 55 s late (14:50:55).");
    expect(within(card).getByText("Operator").nextElementSibling).toHaveTextContent("Aomori City Bus");
    expect(within(card).getByText("Date").nextElementSibling).toHaveTextContent("Wed, Sep 2, 2026");
    expect(within(card).getByText("Stop").nextElementSibling).toHaveTextContent("Aomori Station");
    expect(within(card).getByText("Actual departure").nextElementSibling).toHaveTextContent("14:50:55");
    await userEvent.click(within(card).getByRole("button", { name: "Print / Save as PDF" }));
    expect(print).toHaveBeenCalledOnce();
  });

  it("says the time is at the trip's first timetabled stop, which may not be where the passenger boarded", async () => {
    mockReport({ data: certificate(ROWS) });
    renderLookup();
    const card = await chooseDeparture("14:45 from Aomori Station (5 min 55 s late)");
    expect(card).toHaveTextContent(
      "This is the departure time at Aomori Station, on the side where the trip starts. It can differ from the time at the stop you boarded.",
    );
    try {
      await i18n.changeLanguage("ja");
      const ja = await screen.findByRole("region", { name: "選んだ便の遅延の記録" });
      expect(ja).toHaveTextContent("14:45発の便は、Aomori Stationを5分55秒遅れて発車しました（14:50:55）。");
      expect(ja).toHaveTextContent("始発側の「Aomori Station」での発車時刻です。乗車した停留所での時刻とは異なることがあります。");
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("says a stop recorded after the trip's start is not where the trip starts", async () => {
    mockReport({ data: certificate(ROWS) });
    renderLookup();
    const card = await chooseDeparture("15:15 from Furukawa (1 min 10 s late)");
    expect(card).toHaveTextContent("The 15:15 departure left Furukawa 1 min 10 s late (15:16:10).");
    expect(card).toHaveTextContent(
      "This is the departure time at Furukawa, the first stop the real-time data recorded for this trip, which is not where it starts. It can differ from the time at the stop you boarded.",
    );
    expect(card).not.toHaveTextContent("on the side where the trip starts");
  });

  it("says the time is at the first recorded stop when the timetable no longer names it", async () => {
    mockReport({ data: certificate([["Aomori City Bus", "W54", "weekday", "2026-09-02", "16:00:00", "16:02:00", 120, null, null]]) });
    renderLookup();
    const card = await chooseDeparture("16:00 (2 min late)");
    expect(card).toHaveTextContent("The 16:00 departure left its first recorded stop 2 min late (16:02).");
    expect(card).toHaveTextContent(
      "Times are at the first stop recorded for this trip in the operator's real-time feed. It can differ from the time at the stop you boarded.",
    );
    expect(within(card).getByText("Stop").nextElementSibling).toHaveTextContent("—");
  });

  it("says on the record that the service made it from the operator's data and that it is no certificate", async () => {
    mockReport({ data: certificate(ROWS) });
    renderLookup();
    const card = await chooseDeparture("14:45 from Aomori Station (5 min 55 s late)");
    expect(card).toHaveTextContent(
      "This record is a reference document this service made from the real-time data that Aomori City Bus publishes. " +
        "It is not a delay certificate issued by the operator.",
    );
    try {
      await i18n.changeLanguage("ja");
      const ja = await screen.findByRole("region", { name: "選んだ便の遅延の記録" });
      expect(ja).toHaveTextContent(
        "この記録は、Aomori City Busが公開しているリアルタイムデータをもとに本サービスが作成した参考資料です。" +
          "事業者が発行する遅延証明書ではありません。",
      );
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("keeps recording the chosen departure when a refetch adds an earlier one", async () => {
    let rows = ROWS;
    vi.spyOn(hooks, "useReport").mockImplementation(
      (_aid, reportType) =>
        ({ data: reportType == null ? undefined : certificate(rows), error: null, isPlaceholderData: false, refetch: vi.fn() }) as never,
    );
    const { rerender } = renderLookup();
    await chooseRoute();
    await userEvent.selectOptions(screen.getByLabelText("Departure"), "15:15 from Furukawa (1 min 10 s late)");
    rows = [["Aomori City Bus", "W54", "weekday", "2026-09-02", "14:15:00", "14:17:00", 120, "Aomori Station", true], ...ROWS];
    rerender(<Lookup />);
    const card = screen.getByRole("region", { name: "Delay record for the chosen departure" });
    expect(within(card).getByText("Scheduled departure").nextElementSibling).toHaveTextContent("15:15");
  });

  it("drops the chosen departure when the day changes", async () => {
    vi.spyOn(hooks, "useReport").mockImplementation(
      (_aid, reportType, scope) =>
        ({
          data: reportType == null ? undefined : certificate(ROWS.map((r) => [r[0], r[1], r[2], scope.from, r[4], r[5], r[6], r[7], r[8]])),
          error: null,
          isPlaceholderData: false,
          refetch: vi.fn(),
        }) as never,
    );
    renderLookup();
    await chooseDeparture("14:45 from Aomori Station (5 min 55 s late)");
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-09-01" } });
    expect(screen.queryByRole("region", { name: "Delay record for the chosen departure" })).toBeNull();
  });

  it("says so when the route ran no late departure that day", async () => {
    mockReport({ data: certificate([]) });
    renderLookup();
    await chooseRoute();
    expect(screen.getByText("No departure of this route left late on this day.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Departure")).toBeNull();
  });

  it("says the day has no data, not that nothing left late, when the feed reported nothing", async () => {
    mockReport({ data: { ...certificate([]), observed: false } });
    renderLookup();
    await chooseRoute();
    expect(
      screen.getByText("There is no live-feed data for this route on this day, so whether a departure left late can't be checked."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No departure of this route left late on this day.")).toBeNull();
    expect(screen.queryByText(/wasn't in the live feed/)).toBeNull();
  });

  it("says whether the day had data couldn't be checked, rather than that nothing left late", async () => {
    mockReport({ data: { ...certificate([]), observed: null } });
    renderLookup();
    await chooseRoute();
    expect(
      screen.getByText("No late departure was found; whether the feed reported this day couldn't be checked."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No departure of this route left late on this day.")).toBeNull();
  });

  it("shows another route's departures as still loading, not as this route's", async () => {
    mockReport({ data: certificate(ROWS), isPlaceholderData: true });
    renderLookup();
    await chooseRoute();
    expect(screen.getByText("Looking up late departures…")).toBeInTheDocument();
    expect(screen.queryByLabelText("Departure")).toBeNull();
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

  it("prints the record and its disclaimer without the controls that found it", () => {
    const print = ruleBody(readFileSync(resolve(__dirname, "./DelayCertificateLookup.css"), "utf8"), "@media print");
    expect(decl(ruleBody(print, ".cert-lookup__controls"), "display")).toBe("none");
    expect(decl(ruleBody(print, ".cert-card__print"), "display")).toBe("none");
    expect(print).not.toContain(".cert-card__disclaimer");
  });
});
