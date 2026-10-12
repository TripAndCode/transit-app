import { act, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as hooks from "../api/hooks";
import { renderWithProviders } from "../test/renderWithProviders";
import { formatDateTime } from "../utils/format";
import { PrintFrame } from "./PrintFrame";

const AGENCIES = [{ agency_id: 5, agency_name: "Aomori Bus" }];
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
