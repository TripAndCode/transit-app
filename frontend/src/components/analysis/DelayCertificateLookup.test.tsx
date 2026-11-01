import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useState } from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import { decl, ruleBody } from "../../test/cssRules";
import * as hooks from "../../api/hooks";

import type { DelayRecordStop, DelayRecordStopsResponse, DelayRecordTrip, DelayRecordTripsResponse } from "../../api/types";
import i18n from "../../i18n";
import { DelayCertificateLookup } from "./DelayCertificateLookup";
import { STILL_WORKING_AFTER_MS } from "../StillWorking";

const ROUTE = { route_id: "r1", route_short_name: "W54", route_long_name: null, route_code: "W54", trip_headsigns: [] };
const OTHER_ROUTE = { route_id: "r2", route_short_name: "K10", route_long_name: null, route_code: "K10", trip_headsigns: [] };

const LATEST_DAY = "2026-09-02";
const AGENCY = "Hiroshima Dentetsu";

const at = (clock: string) => {
  const [h, m, s = "0"] = clock.split(":");
  return Number(h) * 3600 + Number(m) * 60 + Number(s);
};

const TRIPS: DelayRecordTrip[] = [
  { trip_id: "T1742", departure_sec: at("17:42"), terminal: "Kumano", headsign: null, matched: true, observed_stops: 30 },
  { trip_id: "T1800", departure_sec: at("18:00"), terminal: null, headsign: null, matched: true, observed_stops: 4 },
  { trip_id: "TX", departure_sec: at("18:04"), terminal: null, headsign: null, matched: false, observed_stops: 2 },
  { trip_id: "T1830", departure_sec: at("18:30"), terminal: "Hiroshima Station", headsign: "via Hatchobori", matched: true, observed_stops: 20 },
];

const T1742_STOPS: DelayRecordStop[] = [
  { stop_sequence: 1, stop_name: "Bus Center", scheduled_sec: at("17:42"), dep_delay: 0 },
  { stop_sequence: 2, stop_name: "Kamiyacho-higashi", scheduled_sec: at("17:46"), dep_delay: null },
  { stop_sequence: 3, stop_name: "Hatchobori", scheduled_sec: at("17:50"), dep_delay: 480 },
  { stop_sequence: 4, stop_name: "Danbara", scheduled_sec: at("17:55"), dep_delay: -30 },
  { stop_sequence: 5, stop_name: "Kumano", scheduled_sec: at("18:30"), dep_delay: null },
];

const TX_STOPS: DelayRecordStop[] = [
  { stop_sequence: 2, stop_name: null, scheduled_sec: at("18:04"), dep_delay: 240 },
  { stop_sequence: 3, stop_name: null, scheduled_sec: at("18:08:30"), dep_delay: 300 },
];

const STOPS: Record<string, DelayRecordStop[]> = { T1742: T1742_STOPS, TX: TX_STOPS, T1800: [] };

function tripsResponse(trips: DelayRecordTrip[], route = "W54", date = LATEST_DAY): DelayRecordTripsResponse {
  return { date, route, trips, scope_applied: {} };
}

function stopsResponse(trip: string, route = "W54", date = LATEST_DAY): DelayRecordStopsResponse {
  const stops = STOPS[trip] ?? [];
  return {
    date,
    route,
    agency_name: AGENCY,
    trip: stops.length ? TRIPS.find((t) => t.trip_id === trip)! : null,
    stops,
    scope_applied: {},
  };
}

type QueryState<T> = { data?: T; error?: Error | null };

function mockSteps({
  trips = { data: tripsResponse(TRIPS) },
  stops,
}: {
  trips?: QueryState<DelayRecordTripsResponse>;
  stops?: (trip: string) => QueryState<DelayRecordStopsResponse>;
} = {}) {
  const tripsHook = vi.spyOn(hooks, "useDelayRecordTrips").mockImplementation(
    (_aid, _date, route) =>
      ({ data: undefined, error: null, refetch: vi.fn(), ...(route == null ? {} : trips) }) as never,
  );
  const stopsHook = vi.spyOn(hooks, "useDelayRecordStops").mockImplementation(
    (_aid, date, route, trip) =>
      ({
        data: undefined,
        error: null,
        refetch: vi.fn(),
        ...(trip == null ? {} : stops ? stops(trip) : { data: stopsResponse(trip, route!, date) }),
      }) as never,
  );
  return { tripsHook, stopsHook };
}

/** The lookup as its section holds it: the section keeps the day, so the
 *  staff list below can follow it. */
function Lookup() {
  const [date, setDate] = useState(LATEST_DAY);
  return <DelayCertificateLookup aid={1} date={date} latestDay={LATEST_DAY} onDateChange={setDate} />;
}

function renderLookup() {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [ROUTE, OTHER_ROUTE], isLoading: false } as never);
  return renderWithProviders(<Lookup />);
}

async function chooseRoute(name: RegExp = /W54/) {
  await userEvent.click(screen.getByRole("button", { name: "Route" }));
  await userEvent.click(screen.getByRole("option", { name }));
}

async function chooseTrip(option: string) {
  await userEvent.selectOptions(screen.getByLabelText("Trip"), option);
}

async function chooseStop(option: string) {
  await userEvent.selectOptions(screen.getByLabelText("Stop you boarded at"), option);
  return screen.getByRole("region", { name: "Delay record for the chosen departure" });
}

function optionTexts(label: string) {
  return within(screen.getByLabelText(label))
    .getAllByRole("option")
    .map((o) => o.textContent);
}

function fact(card: HTMLElement, label: string) {
  return within(card).getByText(label).nextElementSibling;
}

describe("DelayCertificateLookup", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("opens on the latest data day and offers no day after it", () => {
    mockSteps();
    renderLookup();
    const date = screen.getByLabelText("Date");
    expect(date).toHaveValue(LATEST_DAY);
    expect(date).toHaveAttribute("max", LATEST_DAY);
  });

  it("keeps a typed day after the data's last day to that last day", () => {
    mockSteps();
    renderLookup();
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-09-05" } });
    expect(screen.getByLabelText("Date")).toHaveValue(LATEST_DAY);
  });

  it("numbers its four steps: the day, the route, the trip, then the stop boarded at", async () => {
    mockSteps();
    const { container } = renderLookup();
    await chooseRoute();
    await chooseTrip("17:42 to Kumano");
    const steps = [...container.querySelectorAll(".cert-lookup__step")].map((step) => step.parentElement!.textContent);
    expect(steps.map((text) => text?.slice(0, 1))).toEqual(["1", "2", "3", "4"]);
    expect(steps[2]).toContain("Trip");
    expect(steps[3]).toContain("Stop you boarded at");
  });

  it("asks for nothing until a route is chosen, then for that route's trips on the day", async () => {
    const { tripsHook, stopsHook } = mockSteps();
    renderLookup();
    expect(tripsHook.mock.calls.every((call) => call[2] == null)).toBe(true);
    await chooseRoute();
    expect(tripsHook.mock.calls.at(-1)).toEqual([1, LATEST_DAY, "W54"]);
    expect(stopsHook.mock.calls.every((call) => call[3] == null)).toBe(true);
    await chooseTrip("17:42 to Kumano");
    expect(stopsHook.mock.calls.at(-1)).toEqual([1, LATEST_DAY, "W54", "T1742"]);
  });

  it("lists every trip of the day by its departure, where it runs to and the feed's headsign, not only late ones", async () => {
    mockSteps();
    renderLookup();
    await chooseRoute();
    expect(optionTexts("Trip")).toEqual([
      "Choose a trip",
      "17:42 to Kumano",
      "18:00",
      "First recorded at 18:04 (not matched to the timetable)",
      "18:30 to Hiroshima Station (via Hatchobori)",
    ]);
    expect(screen.getByText("A trip that isn't listed has no record in the live feed for this day.")).toBeInTheDocument();
    try {
      await i18n.changeLanguage("ja");
      expect(optionTexts("便").slice(1)).toEqual([
        "17:42発 Kumano行き",
        "18:00発",
        "最初の記録 18:04（時刻表と照合できず）",
        "18:30発 Hiroshima Station行き（via Hatchobori）",
      ]);
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("lists the trip's stops in order with their times, marks the recorded ones, and leaves out an unrecorded last stop", async () => {
    mockSteps();
    renderLookup();
    await chooseRoute();
    await chooseTrip("17:42 to Kumano");
    expect(optionTexts("Stop you boarded at")).toEqual([
      "Choose a stop",
      "17:42 Bus Center (recorded)",
      "17:46 Kamiyacho-higashi",
      "17:50 Hatchobori (recorded)",
      "17:55 Danbara (recorded)",
    ]);
  });

  it("records the departure delay at the stop boarded, timed by that stop's own schedule, ready to print", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    mockSteps();
    renderLookup();
    await chooseRoute();
    await chooseTrip("17:42 to Kumano");
    const card = await chooseStop("17:50 Hatchobori (recorded)");
    expect(card).toHaveTextContent("The 17:50 departure left Hatchobori 8 min late (17:58).");
    expect(fact(card, "Operator")).toHaveTextContent(AGENCY);
    expect(fact(card, "Date")).toHaveTextContent("Wed, Sep 2, 2026");
    expect(fact(card, "Trip")).toHaveTextContent("17:42 to Kumano");
    expect(fact(card, "Stop")).toHaveTextContent("Hatchobori");
    expect(fact(card, "Scheduled departure")).toHaveTextContent("17:50");
    expect(fact(card, "Actual departure")).toHaveTextContent("17:58");
    expect(fact(card, "Delay")).toHaveTextContent("8 min late");
    expect(card).toHaveTextContent("The delay is the last departure delay the real-time data reported for this stop.");
    await userEvent.click(within(card).getByRole("button", { name: "Print / Save as PDF" }));
    expect(print).toHaveBeenCalledOnce();
    try {
      await i18n.changeLanguage("ja");
      const ja = await screen.findByRole("region", { name: "選んだ便の遅延の記録" });
      expect(ja).toHaveTextContent("17:50発の便は、Hatchoboriを8分遅れて発車しました（17:58）。");
      expect(within(ja).getByText("便").nextElementSibling).toHaveTextContent("17:42発 Kumano行き");
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("says a departure on time or early as such, not as a delay", async () => {
    mockSteps();
    renderLookup();
    await chooseRoute();
    await chooseTrip("17:42 to Kumano");
    const onTime = await chooseStop("17:42 Bus Center (recorded)");
    expect(onTime).toHaveTextContent("The 17:42 departure left Bus Center on time (17:42).");
    expect(fact(onTime, "Delay")).toHaveTextContent("on time");
    const early = await chooseStop("17:55 Danbara (recorded)");
    expect(early).toHaveTextContent("The 17:55 departure left Danbara 30 s early (17:54:30).");
    expect(fact(early, "Delay")).toHaveTextContent("30 s early");
  });

  it("says plainly that a stop with no reading has no record, naming the recorded stops either side", async () => {
    mockSteps();
    renderLookup();
    await chooseRoute();
    await chooseTrip("17:42 to Kumano");
    const card = await chooseStop("17:46 Kamiyacho-higashi");
    expect(card).toHaveTextContent("No departure from Kamiyacho-higashi was recorded for the 17:46 departure in the real-time data.");
    expect(card).toHaveTextContent("Recorded before it: Bus Center (on time)");
    expect(card).toHaveTextContent("Recorded after it: Hatchobori (8 min late)");
    expect(fact(card, "Scheduled departure")).toHaveTextContent("17:46");
    expect(fact(card, "Actual departure")).toHaveTextContent("—");
    expect(fact(card, "Delay")).toHaveTextContent("—");
    try {
      await i18n.changeLanguage("ja");
      const ja = await screen.findByRole("region", { name: "選んだ便の遅延の記録" });
      expect(ja).toHaveTextContent("17:46発の便のKamiyacho-higashiでの発車は、リアルタイムデータに記録がありません。");
      expect(ja).toHaveTextContent("前に記録された停留所：Bus Center（定刻）");
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("never names a stop or a start time the timetable could not match for that day", async () => {
    mockSteps();
    renderLookup();
    await chooseRoute();
    await chooseTrip("First recorded at 18:04 (not matched to the timetable)");
    expect(optionTexts("Stop you boarded at")).toEqual([
      "Choose a stop",
      "18:04 Stop (not matched to the timetable) (recorded)",
      "18:08:30 Stop (not matched to the timetable) (recorded)",
    ]);
    const card = await chooseStop("18:08:30 Stop (not matched to the timetable) (recorded)");
    expect(card).toHaveTextContent("The 18:08:30 departure left Stop (not matched to the timetable) 5 min late (18:13:30).");
    expect(card).toHaveTextContent(
      "This trip couldn't be matched to the timetable for this day, so its stop names and start time can't be shown.",
    );
    try {
      await i18n.changeLanguage("ja");
      const ja = await screen.findByRole("region", { name: "選んだ便の遅延の記録" });
      expect(ja).toHaveTextContent("18:08:30発の便は、停留所（時刻表と照合できず）を5分遅れて発車しました（18:13:30）。");
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("calls the trip this trip when a reading carries no schedule, with no time it cannot know", async () => {
    mockSteps({
      stops: () => ({
        data: {
          ...stopsResponse("T1742"),
          stops: [{ stop_sequence: 1, stop_name: "Bus Center", scheduled_sec: null, dep_delay: 120 }],
        },
      }),
    });
    renderLookup();
    await chooseRoute();
    await chooseTrip("17:42 to Kumano");
    const card = await chooseStop("— Bus Center (recorded)");
    expect(card).toHaveTextContent("This trip left Bus Center 2 min late (—).");
    expect(fact(card, "Actual departure")).toHaveTextContent("—");
  });

  it("shows a time after midnight as the next day's", async () => {
    const late: DelayRecordTrip = { trip_id: "TN", departure_sec: at("24:10"), terminal: "Depot", headsign: null, matched: true, observed_stops: 2 };
    mockSteps({
      trips: { data: tripsResponse([late]) },
      stops: () => ({
        data: {
          ...stopsResponse("TN"),
          trip: late,
          stops: [
            { stop_sequence: 1, stop_name: "Bus Center", scheduled_sec: at("24:10"), dep_delay: 120 },
            { stop_sequence: 2, stop_name: "Depot", scheduled_sec: at("24:30"), dep_delay: 120 },
          ],
        },
      }),
    });
    renderLookup();
    await chooseRoute();
    expect(optionTexts("Trip")).toContain("00:10 (next day) to Depot");
    await chooseTrip("00:10 (next day) to Depot");
    const card = await chooseStop("00:10 (next day) Bus Center (recorded)");
    expect(card).toHaveTextContent("The 00:10 (next day) departure left Bus Center 2 min late (00:12 (next day)).");
    try {
      await i18n.changeLanguage("ja");
      const ja = await screen.findByRole("region", { name: "選んだ便の遅延の記録" });
      expect(ja).toHaveTextContent("翌00:10発の便は、Bus Centerを2分遅れて発車しました（翌00:12）。");
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("says on the record that the service made it from the operator's data and that it is no certificate", async () => {
    mockSteps();
    renderLookup();
    await chooseRoute();
    await chooseTrip("17:42 to Kumano");
    const card = await chooseStop("17:50 Hatchobori (recorded)");
    expect(card).toHaveTextContent(
      `This record is a reference document this service made from the real-time data that ${AGENCY} publishes. ` +
        "It is not a delay certificate issued by the operator.",
    );
    try {
      await i18n.changeLanguage("ja");
      const ja = await screen.findByRole("region", { name: "選んだ便の遅延の記録" });
      expect(ja).toHaveTextContent(
        `この記録は、${AGENCY}が公開しているリアルタイムデータをもとに本サービスが作成した参考資料です。` +
          "事業者が発行する遅延証明書ではありません。",
      );
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("drops the chosen trip and stop when the day changes, though the same trip runs that day too", async () => {
    mockSteps();
    renderLookup();
    await chooseRoute();
    await chooseTrip("17:42 to Kumano");
    await chooseStop("17:50 Hatchobori (recorded)");
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2026-09-01" } });
    expect(screen.queryByRole("region", { name: "Delay record for the chosen departure" })).toBeNull();
    expect(screen.getByLabelText("Trip")).toHaveValue("");
    expect(screen.queryByLabelText("Stop you boarded at")).toBeNull();
  });

  it("drops the chosen stop when another trip is chosen", async () => {
    mockSteps();
    renderLookup();
    await chooseRoute();
    await chooseTrip("17:42 to Kumano");
    await chooseStop("17:50 Hatchobori (recorded)");
    await chooseTrip("First recorded at 18:04 (not matched to the timetable)");
    expect(screen.queryByRole("region", { name: "Delay record for the chosen departure" })).toBeNull();
    expect(screen.getByLabelText("Stop you boarded at")).toHaveValue("");
  });

  it("says the day has no data when the feed recorded no trip of the route", async () => {
    mockSteps({ trips: { data: tripsResponse([]) } });
    renderLookup();
    await chooseRoute();
    expect(screen.getByText("There is no live-feed data for this route on this day.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Trip")).toBeNull();
  });

  it("says a chosen trip has no record that day when none of its stops has one", async () => {
    mockSteps();
    renderLookup();
    await chooseRoute();
    await chooseTrip("18:00");
    expect(screen.getByText("This trip has no record for this day.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Stop you boarded at")).toBeNull();
  });

  it("says a slow lookup is still working, with nothing narrower to offer for one day", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockSteps({ trips: {} });
      renderLookup();
      await chooseRoute();
      expect(screen.getByText("Looking up trips…")).toBeInTheDocument();
      act(() => vi.advanceTimersByTime(STILL_WORKING_AFTER_MS));
      expect(screen.getByText("Still working on it…")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Narrow/ })).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says a trip's stops are loading until they arrive", async () => {
    mockSteps({ stops: () => ({}) });
    renderLookup();
    await chooseRoute();
    await chooseTrip("17:42 to Kumano");
    expect(screen.getByText("Loading the trip's stops…")).toBeInTheDocument();
  });

  it("offers a retry when either lookup fails", async () => {
    mockSteps({ trips: { error: new Error("boom") } });
    renderLookup();
    await chooseRoute();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    cleanup();
    vi.restoreAllMocks();
    mockSteps({ stops: () => ({ error: new Error("boom") }) });
    renderLookup();
    await chooseRoute();
    await chooseTrip("17:42 to Kumano");
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("prints the record and its disclaimer without the controls that found it", () => {
    const print = ruleBody(readFileSync(resolve(__dirname, "./DelayCertificateLookup.css"), "utf8"), "@media print");
    expect(decl(ruleBody(print, ".cert-lookup__controls"), "display")).toBe("none");
    expect(decl(ruleBody(print, ".cert-card__print"), "display")).toBe("none");
    expect(print).not.toContain(".cert-card__disclaimer");
  });
});
