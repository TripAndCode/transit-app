import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import type { TransferPairSummary, TransferReliability, TransferTally } from "../../api/types";
import { TransfersView } from "./TransfersView";

const side = (agency: string, route: string, stop: string) => ({
  agency_id: 9,
  agency_name: agency,
  route_code: route,
  route_name: route,
  direction: null,
  stop_ids: [stop],
  stop_names: ["Nukushina 4-chome"],
});
const PAIR: TransferPairSummary = {
  id: 3,
  name: "Nukushina 29 to 2",
  arriving: side("Hiroshima Bus", "29", "50271 0"),
  departing: side("Hiroden", "2", "50271 1"),
  walk_sec: 120,
  day_types: ["weekday", "saturday"],
};
const OTHER: TransferPairSummary = { ...PAIR, id: 4, name: "Other hub", day_types: ["sunday_holiday"] };

const tally = (over: Partial<TransferTally> = {}): TransferTally => ({
  planned: 40,
  held: 30,
  missed: 6,
  unobserved: 4,
  stranded: 0,
  held_share: 30 / 36,
  small_sample: false,
  ...over,
});

const REPORT: TransferReliability = {
  pair: PAIR,
  from_date: "2026-10-01",
  to_date: "2026-10-28",
  day_type: "weekday",
  min_samples: 21,
  buffer_share: 0.95,
  total: tally(),
  held_share: 30 / 36,
  no_onward: 0,
  waits: 34,
  wait_p50_sec: 360,
  wait_p90_sec: 960,
  wait_bands: [
    { max_sec: 300, share: 0.4 },
    { max_sec: 600, share: 0.8 },
    { max_sec: 1200, share: 0.97 },
  ],
  buffer_min: 7,
  by_hour: [
    { hour: 7, ...tally({ planned: 20, held: 18, missed: 1, unobserved: 1 }) },
    { hour: 24, ...tally({ planned: 2, held: 1, missed: 0, unobserved: 1, small_sample: true }) },
  ],
  by_day: [],
  worst_days: [{ day: "2026-10-14", ...tally({ planned: 3, held: 1, missed: 2, unobserved: 0, held_share: 1 / 3, small_sample: true }) }],
  no_plan_days: ["2026-10-02"],
  definition: {} as TransferReliability["definition"],
};

function stub(pairs: TransferPairSummary[], report: TransferReliability = REPORT) {
  const fetchMock = vi.fn((url: string) => {
    const body = url.includes("/reliability") ? report : { pairs };
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function open(search = "") {
  const router = createMemoryRouter([{ path: "agencies/:agencyId/compare", element: <TransfersView /> }], {
    initialEntries: [`/agencies/9/compare?by=transfers${search}`],
  });
  renderWithProviders(<RouterProvider router={router} />);
  return router;
}

describe("TransfersView", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("says no transfer is defined when the agency has none", async () => {
    stub([]);
    open();
    expect(await screen.findByText("No transfer is defined for this agency")).toBeInTheDocument();
  });

  it("shows held and missed, the buffer as a feed-based line, the wait bands and the worst days", async () => {
    const fetchMock = stub([PAIR, OTHER]);
    open();
    const line = await screen.findByTestId("transfers-recommendation");
    expect(line).toHaveTextContent("A planned buffer of 7 min would have held 95% of observed weekday connections.");
    expect(line).toHaveTextContent("Feed-based");
    expect(screen.getByText("Within 10 min").parentElement).toHaveTextContent("80%");
    expect(screen.getByText("24:00")).toBeInTheDocument();
    const worst = screen.getByRole("heading", { name: "Days with the most missed connections" }).parentElement!;
    expect(within(worst).getAllByRole("row")).toHaveLength(2);
    expect(screen.getByText(/1 day is left out/)).toBeInTheDocument();
    const reliability = fetchMock.mock.calls.map(([u]) => String(u)).find((u) => u.includes("/reliability"))!;
    expect(reliability).toContain("/api/9/transfer-pairs/3/reliability?day_type=weekday");
  });

  it("withholds the share, buffer and waits below the sample floor", async () => {
    stub([PAIR], {
      ...REPORT,
      held_share: null,
      buffer_min: null,
      wait_bands: null,
      wait_p50_sec: null,
      wait_p90_sec: null,
      waits: 5,
    });
    open();
    expect(await screen.findByText("Fewer than 21 connections were observed, so no share or buffer is given.")).toBeInTheDocument();
    expect(screen.queryByTestId("transfers-recommendation")).toBeNull();
    expect(screen.getByText(/The wait is known for 5 connections/)).toBeInTheDocument();
  });

  it("picks the pair and its day type from the URL and writes a switch back", async () => {
    const fetchMock = stub([PAIR, OTHER]);
    const router = open("&pair=3&pair_day=saturday");
    await screen.findByTestId("transfers-recommendation");
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/transfer-pairs/3/reliability?day_type=saturday"))).toBe(true);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "4" } });
    await waitFor(() => expect(new URLSearchParams(router.state.location.search).get("pair")).toBe("4"));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/transfer-pairs/4/reliability?day_type=sunday_holiday"))).toBe(true),
    );
  });
});
