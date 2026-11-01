import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as hooks from "../api/hooks";
import i18n from "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import { formatDateTime } from "../utils/format";
import { PrintCredit, PrintFrame } from "./PrintFrame";

const AOMORI_FEED = "https://aomoricitybus.com/TripUpdate.pb";
const HIROSHIMA_FEED = "https://ajt-mobusta-gtfs.mcapps.jp/realtime/8/trip_updates.bin";
const AGENCIES = [
  { agency_id: 5, agency_name: "Aomori Bus", feed_url: AOMORI_FEED },
  { agency_id: 8, agency_name: "Hiroden", feed_url: HIROSHIMA_FEED },
  { agency_id: 9, agency_name: "Elsewhere Bus", feed_url: "http://example.com/feed.pb" },
];
/** Shared with the backend's CSV credit tests: tests/unit/test_data_sources.py. */
const CREDIT_LINES: Record<string, Record<"ja" | "en", string>> = JSON.parse(
  readFileSync(resolve(__dirname, "../../../tests/fixtures/data_source_credit_lines.json"), "utf8"),
);
const ROUTES = [
  { route_code: "R1", route_short_name: "A1", route_long_name: "Coast Line" },
  { route_code: "R2", route_short_name: "B2", route_long_name: "Hill Line" },
];

function renderFrame(search = "") {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: AGENCIES } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: ROUTES, isLoading: false } as never);
  return renderWithProviders(
    <MemoryRouter initialEntries={[`/agencies/5/map${search}`]}>
      <Routes>
        <Route path="/agencies/:agencyId/*" element={<PrintFrame />} />
        <Route path="*" element={<PrintFrame />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => vi.useFakeTimers({ toFake: ["Date"] }));
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("PrintFrame", () => {
  it("names the agency and every condition that produced the numbers", () => {
    renderFrame("?from=2026-06-01&to=2026-06-30&dow=weekday&time_band=morning&service=平日&routes=R1,R2");
    const header = screen.getByTestId("print-frame-header");
    expect(header).toHaveTextContent("Aomori Bus");
    expect(header).toHaveTextContent("6/1 – 6/30");
    expect(header).toHaveTextContent("weekdays");
    expect(header).toHaveTextContent("Morning");
    expect(header).toHaveTextContent("timetable");
    expect(header).toHaveTextContent("2 routes");
  });

  it("names the selected routes when there are few enough to read", () => {
    renderFrame("?from=2026-06-01&to=2026-06-30&routes=R1,R2");
    const routes = screen.getByTestId("print-frame-routes");
    expect(routes).toHaveTextContent("A1");
    expect(routes).toHaveTextContent("B2");
  });

  it("does not list route names when the selection is too long to print", () => {
    const many = Array.from({ length: 12 }, (_, i) => `R${i}`).join(",");
    renderFrame(`?from=2026-06-01&to=2026-06-30&routes=${many}`);
    expect(screen.getByTestId("print-frame-header")).toHaveTextContent("12 routes");
    expect(screen.queryByTestId("print-frame-routes")).toBeNull();
  });

  it("says all routes when none are selected", () => {
    renderFrame("?from=2026-06-01&to=2026-06-30");
    expect(screen.getByTestId("print-frame-header")).toHaveTextContent("all routes");
    expect(screen.queryByTestId("print-frame-routes")).toBeNull();
  });

  it("stamps the time only when a print starts, not while rendering", () => {
    vi.setSystemTime(new Date("2026-10-11T03:30:00Z"));
    renderFrame("?from=2026-06-01&to=2026-06-30");
    const footer = screen.getByTestId("print-frame-footer");
    expect(footer).not.toHaveTextContent("Generated");

    vi.setSystemTime(new Date("2026-10-11T03:45:00Z"));
    act(() => {
      window.dispatchEvent(new Event("beforeprint"));
    });
    const stamp = formatDateTime("2026-10-11T03:45:00Z", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Tokyo" });
    expect(footer).toHaveTextContent(`Generated ${stamp}`);

    vi.setSystemTime(new Date("2026-10-11T04:10:00Z"));
    act(() => {
      window.dispatchEvent(new Event("beforeprint"));
    });
    expect(footer).toHaveTextContent(
      `Generated ${formatDateTime("2026-10-11T04:10:00Z", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Tokyo" })}`,
    );
  });

  it("renders nothing outside an agency", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: AGENCIES } as never);
    vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/account"]}>
        <PrintFrame />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("print-frame")).toBeNull();
  });
});

function renderCredit(agencyId: number) {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: AGENCIES } as never);
  return renderWithProviders(
    <MemoryRouter initialEntries={[`/agencies/${agencyId}/reports`]}>
      <Routes>
        <Route path="/agencies/:agencyId/*" element={<PrintCredit />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("PrintCredit", () => {
  // Runs before the file's mock restore, so the re-render it causes still
  // reads the mocked agency list.
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  // Paper keeps no link, and CC BY asks that the material and the licence be
  // linked, so each linked name is followed by its URL.
  it("credits an Aomori City Bus printout to its CC BY source, with each URL written out", () => {
    renderCredit(5);
    const credit = screen.getByTestId("print-credit");
    expect(within(credit).getByRole("heading", { name: "Data sources" })).toBeInTheDocument();
    expect([...credit.querySelectorAll("p")].map((p) => p.textContent)).toEqual([CREDIT_LINES[AOMORI_FEED].en]);
    expect(credit.querySelector("a")).toBeNull();
  });

  it("credits a Hiroshima operator's printout to the bus association, in Japanese", async () => {
    await i18n.changeLanguage("ja");
    renderCredit(8);
    const credit = screen.getByTestId("print-credit");
    expect(within(credit).getByRole("heading", { name: "データの出典" })).toBeInTheDocument();
    expect([...credit.querySelectorAll("p")].map((p) => p.textContent)).toEqual([CREDIT_LINES[HIROSHIMA_FEED].ja]);
  });

  it("prints no credit for an agency whose feed no source covers", () => {
    renderCredit(9);
    expect(screen.queryByTestId("print-credit")).toBeNull();
  });

  it("prints no credit outside an agency", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: AGENCIES } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/account"]}>
        <PrintCredit />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("print-credit")).toBeNull();
  });
});
