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

const TWO_AGENCIES = [agency(1, "Aomori City Bus"), agency(9, "Hiroshima Bus")];

function renderPicker(path: string, { agencies = TWO_AGENCIES, isLoading = false, className = undefined as string | undefined } = {}) {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: isLoading ? undefined : agencies, isLoading } as never);
  return renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/agencies/:agencyId/*"
          element={
            <>
              <AgencyPicker className={className} />
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
  it.each([
    ["/agencies/1/time?routes=50", "/agencies/9/time"],
    ["/agencies/1/reports?doc=council&from=2026-08-01", "/agencies/9/reports?doc=council"],
    ["/agencies/1/compare?by=agencies&from=2026-08-01", "/agencies/9/compare?by=agencies"],
    ["/agencies/1/routes/50?tab=stops&routes=50", "/agencies/9/routes"],
  ])("switches agency from %s onto %s, dropping the filters", async (from, to) => {
    const user = userEvent.setup();
    renderPicker(from);
    await user.click(screen.getByRole("button", { name: /Aomori City Bus/ }));
    await user.click(screen.getByRole("option", { name: "Hiroshima Bus" }));
    expect(screen.getByTestId("location").textContent).toBe(to);
  });

  it("closes on Escape and puts focus back on its trigger", async () => {
    renderPicker("/agencies/1/pulse");
    const trigger = screen.getByRole("button", { name: /Aomori City Bus/ });
    await userEvent.click(trigger);
    expect(screen.getByRole("textbox")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it.each([
    ["several agencies", { agencies: TWO_AGENCIES }],
    ["one agency", { agencies: [agency(9, "Hiroshima Bus")] }],
    ["no agency", { agencies: [] }],
    ["agencies still loading", { isLoading: true }],
  ])("lets a className dress the picker with %s", (_, state) => {
    const { container } = renderPicker("/agencies/9/pulse", { ...state, className: "ticket-agency" });
    expect(container.querySelector(".ticket-agency")).not.toBeNull();
    expect(container.querySelector(".ticket-agency button")?.getAttribute("style") ?? null).toBeNull();
  });
});
