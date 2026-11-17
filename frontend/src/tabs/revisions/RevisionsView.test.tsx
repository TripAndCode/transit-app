import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import type { RevisionStopChange, TimetableRevisionDiff, TimetableRevisionsResponse } from "../../api/types";
import { RevisionsView } from "./RevisionsView";

const side = (version: string, first_day: string, last_day: string) => ({ version, content: `${version}c`.slice(0, 8), first_day, last_day });
const APRIL = { first_day: "2026-04-01", before: side("v1", "2026-01-01", "2026-03-31"), after: side("v2", "2026-04-01", "2026-09-30") };
const OCTOBER = { first_day: "2026-10-01", before: side("v2", "2026-04-01", "2026-09-30"), after: side("v3", "2026-10-01", "2027-03-31") };

function stop(over: Partial<RevisionStopChange>): RevisionStopChange {
  return {
    stop_id: "S1",
    stop_name: "Station",
    day_type: "weekday",
    before: 3,
    after: 3,
    lost: 1,
    gained: 1,
    first_before: 7 * 3600,
    first_after: 7 * 3600,
    last_before: 21 * 3600 + 50 * 60,
    last_after: 21 * 3600 + 10 * 60,
    last_shift_min: -40,
    ended: false,
    started: false,
    hours: [
      { hour: 7, before: 2, after: 1 },
      { hour: 8, before: 0, after: 1 },
    ],
    ...over,
  };
}

const DIFF: TimetableRevisionDiff = {
  available: true,
  reason: null,
  revision: APRIL,
  day_types: [
    { day_type: "weekday", before_date: "2026-03-31", after_date: "2026-04-01", departures_before: 9, departures_after: 9, stops_ended: 0, stops_started: 0, last_earlier: 3, last_later: 0 },
    { day_type: "saturday", before_date: "2026-03-28", after_date: "2026-04-04", departures_before: 3, departures_after: 0, stops_ended: 3, stops_started: 0, last_earlier: 0, last_later: 0 },
    { day_type: "sunday_holiday", before_date: null, after_date: "2026-04-05", departures_before: null, departures_after: 3, stops_ended: null, stops_started: null, last_earlier: null, last_later: null },
  ],
  stops: [
    stop({}),
    stop({ stop_id: "S2", stop_name: null, day_type: "saturday", before: 1, after: 0, lost: 1, gained: 0, last_after: null, last_shift_min: null, ended: true, hours: [{ hour: 9, before: 1, after: 0 }] }),
  ],
};

function stubApi(revisions: TimetableRevisionsResponse, diff: TimetableRevisionDiff = DIFF) {
  const fetchMock = vi.fn((url: string) => {
    const body = url.includes("/diff") ? diff : revisions;
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function open(search = "") {
  const router = createMemoryRouter([{ path: "agencies/:agencyId/reports", element: <RevisionsView /> }], {
    initialEntries: [`/agencies/9/reports?doc=revisions${search}`],
  });
  renderWithProviders(<RouterProvider router={router} />);
  return router;
}

describe("RevisionsView", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("says there is nothing to compare when the agency has one timetable", async () => {
    stubApi({ revisions: [] });
    open();
    expect(await screen.findByText("No timetable revision to compare yet")).toBeInTheDocument();
  });

  it("compares the revision `on` names, day type by day type, with a dash for an unknown side", async () => {
    const fetchMock = stubApi({ revisions: [OCTOBER, APRIL] });
    open("&on=2026-04-01");
    const table = (await screen.findByRole("heading", { name: "By day type" })).parentElement!;
    const saturday = within(table).getByRole("row", { name: /Saturday/ });
    expect(saturday).toHaveTextContent("3 → 0");
    expect(within(table).getByRole("row", { name: /Sunday/ })).toHaveTextContent("— → 3");
    expect(screen.getByText(/has no day of that type in force/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/diff?on=2026-04-01"))).toBe(true);
    expect(screen.getByRole("link", { name: /CSV/ }).getAttribute("href")).toContain("on=2026-04-01&format=csv");
  });

  it("lists a day type's changed stops with their lost and gained hours and the last bus", async () => {
    stubApi({ revisions: [APRIL] });
    open();
    const row = (await screen.findByText("Station")).closest("tr")!;
    expect(row).toHaveTextContent("1 / 1");
    expect(row).toHaveTextContent("21:50 → 21:10");
    expect(row).toHaveTextContent("-40 min");
    expect(row).toHaveTextContent("7:00 -1, 8:00 +1");

    fireEvent.click(screen.getByRole("button", { name: "Saturday" }));
    const ended = screen.getByText("S2").closest("tr")!;
    expect(ended).toHaveTextContent("no departures left");
    expect(screen.queryByText("Station")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Sunday & holiday" }));
    expect(screen.getAllByText(/has no day of that type in force/).length).toBeGreaterThan(1);
  });

  it("moves to another revision through the picker", async () => {
    stubApi({ revisions: [OCTOBER, APRIL] });
    const router = open();
    const picker = await screen.findByRole("combobox", { name: "Revision" });
    expect(picker).toHaveValue("2026-10-01");
    fireEvent.change(picker, { target: { value: "2026-04-01" } });
    expect(new URLSearchParams(router.state.location.search).get("on")).toBe("2026-04-01");
    expect(new URLSearchParams(router.state.location.search).get("doc")).toBe("revisions");
  });

  it("says a revision whose calendar is not kept cannot be compared", async () => {
    stubApi({ revisions: [APRIL] }, { available: false, reason: "no_calendar", revision: APRIL, day_types: [], stops: [] });
    open();
    expect(await screen.findByText("This revision cannot be compared")).toBeInTheDocument();
  });

  it("offers every stop past the first fifty", async () => {
    const many = Array.from({ length: 51 }, (_, i) => stop({ stop_id: `S${i}`, stop_name: `Stop ${i}` }));
    stubApi({ revisions: [APRIL] }, { ...DIFF, stops: many });
    open();
    await screen.findByText("Stop 0");
    expect(screen.queryByText("Stop 50")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all 51 stops" }));
    expect(screen.getByText("Stop 50")).toBeInTheDocument();
  });
});
