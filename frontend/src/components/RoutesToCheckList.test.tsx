import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { RoutesToCheckList } from "./RoutesToCheckList";
import * as rangeContext from "../api/rangeContext";
import type { OverviewTopDelayedRoute } from "../api/types";

function routes(): OverviewTopDelayedRoute[] {
  return [
    { route_code: "K31", route_short_name: "観光通り線", avg_min: 6.6 },
    { route_code: "K37", route_short_name: "観光通り線", avg_min: 5.7 },
    { route_code: "W53", route_short_name: null, avg_min: 2.1 },
  ];
}

// RoutesToCheckList calls useRangeContext (react-router-dom's useSearchParams
// under the hood), so — matching the existing pattern in
// RouteForecastSection.test.tsx — every render needs a <MemoryRouter>.
function renderList(rs: OverviewTopDelayedRoute[]) {
  return renderWithProviders(
    <MemoryRouter>
      <RoutesToCheckList routes={rs} />
    </MemoryRouter>,
  );
}

describe("RoutesToCheckList", () => {
  it("groups routes into severity bands with a worst-first header, count, and no empty bands", () => {
    renderList(routes());
    expect(screen.getByText("Routes to check now")).toBeInTheDocument();
    // K31 (6.6) and K37 (5.7) are both >= 5min -> "severe" band, header first
    expect(screen.getByText("> 5 min")).toBeInTheDocument();
    // W53 (2.1) is 1.5-3min -> "mild" band. Note: the real i18n string uses an
    // en-dash with NO surrounding spaces ("1.5–3 min", "3–5 min") -- verified
    // against frontend/src/i18n/locales/en.json, not guessed.
    expect(screen.getByText("1.5–3 min")).toBeInTheDocument();
    // no routes fall in 3-5min ("moderate") or <1.5min ("ok") -- their headers must be absent
    expect(screen.queryByText("3–5 min")).not.toBeInTheDocument();
    expect(screen.queryByText("< 1.5 min")).not.toBeInTheDocument();
  });

  it("shows short_name with the code de-emphasized in parens, not as a separate raw-code column", () => {
    renderList(routes());
    // K31 and K37 share the same short_name -- both rows render it
    expect(screen.getAllByText("観光通り線")).toHaveLength(2);
    expect(screen.getByText("(K31)")).toBeInTheDocument();
    expect(screen.getByText("(K37)")).toBeInTheDocument();
    // W53 has no short_name -- falls back to the bare code, only once (no duplication)
    expect(screen.getAllByText("W53")).toHaveLength(1);
  });

  it("scales each bar relative to the list's own max avg_min", () => {
    renderList(routes());
    const bars = document.querySelectorAll(".ov-check-fill");
    expect(bars).toHaveLength(3);
    expect((bars[0] as HTMLElement).style.width).toBe("100%");
  });

  it("shows the empty-state message when there are no routes", () => {
    renderList([]);
    expect(screen.getByText("No routes need attention")).toBeInTheDocument();
  });

  it("narrows the shared route filter to the clicked route", () => {
    const update = vi.fn();
    vi.spyOn(rangeContext, "useRangeContext").mockReturnValue([
      { from: "2026-06-01", to: "2026-06-07", dow: "all", time_band: "all", service: "all", routes: [] },
      update,
    ]);
    renderList(routes());
    // K31 (6.6) sorts before K37 (5.7) within the "severe" band (worst-first),
    // so the first "観光通り線" match is K31's row.
    fireEvent.click(screen.getAllByText("観光通り線")[0]);
    expect(update).toHaveBeenCalledWith({ routes: ["K31"] });
  });

  it("narrows the filter on Enter and Space, but not on other keys", () => {
    const update = vi.fn();
    vi.spyOn(rangeContext, "useRangeContext").mockReturnValue([
      { from: "2026-06-01", to: "2026-06-07", dow: "all", time_band: "all", service: "all", routes: [] },
      update,
    ]);
    renderList(routes());
    const row = screen.getByText("(K31)").closest('[role="button"]')!;

    fireEvent.keyDown(row, { key: "Tab" });
    expect(update).not.toHaveBeenCalled();

    fireEvent.keyDown(row, { key: "Enter" });
    expect(update).toHaveBeenCalledWith({ routes: ["K31"] });

    update.mockClear();
    fireEvent.keyDown(row, { key: " " });
    expect(update).toHaveBeenCalledWith({ routes: ["K31"] });
  });
});
