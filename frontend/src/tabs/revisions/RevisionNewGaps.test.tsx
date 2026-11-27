import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/renderWithProviders";
import type { RevisionNewGap, TimetableRevisionNewGaps } from "../../api/types";
import { RevisionNewGaps } from "./RevisionNewGaps";

function gap(over: Partial<RevisionNewGap>): RevisionNewGap {
  return {
    stop_id: "S1",
    stop_name: "Station",
    day_type: "weekday",
    departures_before: 6,
    departures_after: 4,
    longest_gap_before: 3600,
    longest_gap_after: 7200,
    ...over,
  };
}

const GAPS: TimetableRevisionNewGaps = {
  available: true,
  reason: null,
  revision: null,
  from_hour: 10,
  to_hour: 16,
  gap_min: 60,
  day_types: [
    { day_type: "weekday", stops: 1 },
    { day_type: "saturday", stops: 0 },
    { day_type: "sunday_holiday", stops: null },
  ],
  stops: [gap({})],
};

function render(body: TimetableRevisionNewGaps) {
  const fetchMock = vi.fn((_url: string) =>
    Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } })),
  );
  vi.stubGlobal("fetch", fetchMock);
  renderWithProviders(<RevisionNewGaps agencyId={9} on="2026-04-01" dayType="weekday" />);
  return fetchMock;
}

const asked = (fetchMock: ReturnType<typeof render>, part: string) => fetchMock.mock.calls.some(([url]) => String(url).includes(part));

describe("RevisionNewGaps", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists the stops left with a new gap over the supply map's default band", async () => {
    const fetchMock = render(GAPS);
    const row = (await screen.findByText("Station")).closest("tr")!;
    expect(row).toHaveTextContent("60 min → 120 min");
    expect(row).toHaveTextContent("6 → 4");
    expect(screen.getByText(/from 10:00 to 16:00 with no departure was 60 min or less .* 1 stops on this day type/)).toBeInTheDocument();
    expect(asked(fetchMock, "/timetable-revisions/new-gaps?on=2026-04-01&day_type=weekday&from_hour=10&to_hour=16&gap_min=60")).toBe(true);
    // The CSV covers every day type, so it names none.
    expect(screen.getByRole("link", { name: /CSV/ })).toHaveAttribute(
      "href",
      expect.stringContaining("/api/9/timetable-revisions/new-gaps?on=2026-04-01&from_hour=10&to_hour=16&gap_min=60&format=csv&lang=en"),
    );
  });

  it("asks again for another band and threshold", async () => {
    const fetchMock = render(GAPS);
    await screen.findByText("Station");
    await userEvent.selectOptions(screen.getByLabelText("Band from"), "6");
    await userEvent.selectOptions(screen.getByLabelText("Gap over"), "90");
    await waitFor(() => expect(asked(fetchMock, "from_hour=6&to_hour=16&gap_min=90")).toBe(true));
  });

  it("asks nothing for a band no gap can exceed the threshold in, and says why", async () => {
    const fetchMock = render(GAPS);
    await screen.findByText("Station");
    await userEvent.selectOptions(screen.getByLabelText("Band to"), "11");
    expect(screen.getByRole("alert")).toHaveTextContent("The gap must be shorter than the band.");
    expect(screen.queryByText("Station")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /CSV/ })).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Band from"), "12");
    expect(screen.getByRole("alert")).toHaveTextContent("The end must be after the start.");
    expect(asked(fetchMock, "to_hour=11")).toBe(false);
  });

  it("says when a day type has no new gap", async () => {
    render({ ...GAPS, day_types: [{ day_type: "weekday", stops: 0 }, ...GAPS.day_types.slice(1)], stops: [] });
    expect(await screen.findByText(/There are none on this day type\./)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("leaves an unknown day type to the stop comparison's explanation", async () => {
    render({ ...GAPS, day_types: [{ day_type: "weekday", stops: null }, ...GAPS.day_types.slice(1)], stops: [] });
    const heading = await screen.findByRole("heading", { name: "New service gaps" });
    await waitFor(() => expect(heading.parentElement!.querySelector("[aria-busy]")).not.toBeNull());
    expect(heading.parentElement!.querySelector("[aria-busy]")).toBeEmptyDOMElement();
  });

  it("lists fifty stops and offers the rest", async () => {
    const many = Array.from({ length: 51 }, (_, i) => gap({ stop_id: `X${i}`, stop_name: `Stop ${i}` }));
    render({ ...GAPS, day_types: [{ day_type: "weekday", stops: 51 }, ...GAPS.day_types.slice(1)], stops: many });
    await screen.findByText("Stop 0");
    expect(screen.queryByText("Stop 50")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all 51 stops" }));
    expect(screen.getByText("Stop 50")).toBeInTheDocument();
  });
});
