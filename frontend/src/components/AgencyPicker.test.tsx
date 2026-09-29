import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import type { Agency } from "../api/types";
import { AgencyPicker } from "./AgencyPicker";

function agency(agency_id: number, agency_name: string): Agency {
  return { agency_id, agency_name, feed_url: "", static_url: null, latest_data_date: null };
}

function Probe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function renderPicker(path: string) {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data: [agency(1, "Aomori City Bus"), agency(9, "Hiroshima Bus")],
    isLoading: false,
  } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/agencies/:agencyId/*"
          element={
            <>
              <AgencyPicker />
              <Probe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AgencyPicker", () => {
  it("keeps the current lens when switching agency, dropping the query", async () => {
    const user = userEvent.setup();
    renderPicker("/agencies/1/analysis/when?routes=50");
    await user.click(screen.getByRole("button", { name: /Aomori City Bus/ }));
    await user.click(screen.getByRole("option", { name: "Hiroshima Bus" }));
    expect(screen.getByTestId("location").textContent).toBe("/agencies/9/analysis/when");
  });

  it("keeps the agencies board when switching agency on it", async () => {
    const user = userEvent.setup();
    renderPicker("/agencies/1/analysis/compare?mode=agencies&from=2026-08-01");
    await user.click(screen.getByRole("button", { name: /Aomori City Bus/ }));
    await user.click(screen.getByRole("option", { name: "Hiroshima Bus" }));
    expect(screen.getByTestId("location").textContent).toBe("/agencies/9/analysis/compare?mode=agencies");
  });
});
