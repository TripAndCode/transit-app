import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import type { Agency } from "../api/types";
import { COMMAND_PALETTE_OPEN_EVENT } from "./commandPaletteEvents";
import { TopBar } from "./TopBar";

function agency(agency_id: number, latest_data_date: string | null): Agency {
  return { agency_id, agency_name: `Agency ${agency_id}`, feed_url: "", static_url: null, latest_data_date };
}

function renderBar(path = "/agencies/9/pulse") {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data: [agency(1, "2026-08-01"), agency(9, "2026-09-29")],
    isLoading: false,
  } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/agencies/:agencyId/*" element={<TopBar />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("TopBar", () => {
  afterEach(() => vi.restoreAllMocks());

  it("opens the command palette from its search field", async () => {
    const onOpen = vi.fn();
    window.addEventListener(COMMAND_PALETTE_OPEN_EVENT, onOpen);
    renderBar();
    await userEvent.click(screen.getByRole("button", { name: /Search routes, reports and screens/ }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    window.removeEventListener(COMMAND_PALETTE_OPEN_EVENT, onOpen);
  });

  it("anchors the first-run tour's Ask step", () => {
    renderBar();
    expect(screen.getByRole("button", { name: /Search routes, reports and screens/ })).toHaveAttribute("data-tour", "ask-nav");
  });

  it("shows the ⌘K shortcut where there is a keyboard", () => {
    renderBar();
    expect(screen.getByText("K")).toBeInTheDocument();
  });

  it("drops the ⌘K shortcut on a touch screen, where it cannot be pressed", () => {
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query: string) =>
        ({
          matches: query === "(pointer: coarse)",
          media: query,
          addEventListener: () => {},
          removeEventListener: () => {},
        }) as unknown as MediaQueryList,
    );
    renderBar();
    expect(screen.queryByText("K")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Search routes, reports and screens/ })).toBeInTheDocument();
  });

  it("states how recent the current agency's data is", () => {
    renderBar();
    expect(screen.getByText("Data through Sep 29, 2026")).toBeInTheDocument();
  });

  it("states nothing about freshness while the agency has no data yet", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [agency(9, null)], isLoading: false } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/9/pulse"]}>
        <Routes>
          <Route path="/agencies/:agencyId/*" element={<TopBar />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.queryByText(/Data through/)).toBeNull();
  });
});
