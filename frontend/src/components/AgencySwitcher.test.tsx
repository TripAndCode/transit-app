import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { AgencySwitcher } from "./AgencySwitcher";
import { openAgencySwitcher } from "./agencySwitcherEvents";
import { readRecentAgencies } from "../api/recentAgencies";
import * as hooks from "../api/hooks";
import type { Agency } from "../api/types";

const agencies: Agency[] = [
  { agency_id: 1, agency_name: "Hokuriku Transit", feed_url: "", static_url: null, latest_data_date: null },
  { agency_id: 2, agency_name: "Kaga Bay Bus", feed_url: "", static_url: null, latest_data_date: "2026-06-09" },
  { agency_id: 3, agency_name: "Noto Rail", feed_url: "", static_url: null, latest_data_date: "2026-01-02" },
];

function Probe() {
  const location = useLocation();
  return (
    <>
      <div data-testid="pathname">{location.pathname}</div>
      <div data-testid="search">{location.search}</div>
    </>
  );
}

const FILTERED_PATH = "/agencies/1/reports?from=2026-06-01&to=2026-06-07&dow=weekday";

function renderSwitcher(path = FILTERED_PATH, onSwitch = vi.fn()) {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: agencies, isLoading: false } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/agencies/:agencyId/*"
          element={
            <>
              <AgencySwitcher onSwitch={onSwitch} />
              <Probe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
  return { onSwitch };
}

function trigger() {
  return screen.getByRole("button", { name: /Hokuriku Transit/ });
}

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-06-10T09:00:00+09:00"));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("AgencySwitcher", () => {
  it("shows the current agency and keeps the list closed until asked", () => {
    renderSwitcher();
    expect(trigger()).toHaveAttribute("aria-haspopup", "listbox");
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("opens a listbox of every agency", async () => {
    const user = userEvent.setup();
    renderSwitcher();
    await user.click(trigger());
    const listbox = screen.getByRole("listbox");
    expect(within(listbox).getAllByRole("option")).toHaveLength(3);
    expect(trigger()).toHaveAttribute("aria-expanded", "true");
  });

  it("lists recently visited agencies before the rest", async () => {
    localStorage.setItem("transit.recentAgencies", JSON.stringify([3, 2]));
    const user = userEvent.setup();
    renderSwitcher();
    await user.click(trigger());
    const names = screen.getAllByRole("option").map((o) => o.textContent);
    expect(names[0]).toContain("Noto Rail");
    expect(names[1]).toContain("Kaga Bay Bus");
    expect(names[2]).toContain("Hokuriku Transit");
  });

  it("labels each agency with how fresh its data is", async () => {
    const user = userEvent.setup();
    renderSwitcher();
    await user.click(trigger());
    expect(screen.getByRole("option", { name: /Kaga Bay Bus/ }).textContent).toContain("Up to date");
    expect(screen.getByRole("option", { name: /Noto Rail/ }).textContent).toContain("159d ago");
    expect(screen.getByRole("option", { name: /Hokuriku Transit/ }).textContent).toContain("No data yet");
  });

  it("keeps the current tab and filter context when switching", async () => {
    const user = userEvent.setup();
    const { onSwitch } = renderSwitcher();
    await user.click(trigger());
    await user.click(screen.getByRole("option", { name: /Kaga Bay Bus/ }));
    expect(screen.getByTestId("pathname")).toHaveTextContent("/agencies/2/reports");
    expect(screen.getByTestId("search")).toHaveTextContent("from=2026-06-01&to=2026-06-07&dow=weekday");
    expect(onSwitch).toHaveBeenCalledWith(agencies[1]);
  });

  it("remembers the agency it switched to", async () => {
    const user = userEvent.setup();
    renderSwitcher();
    await user.click(trigger());
    await user.click(screen.getByRole("option", { name: /Noto Rail/ }));
    expect(readRecentAgencies()[0]).toBe(3);
  });

  it("moves the active option with type-ahead and switches on Enter", async () => {
    const user = userEvent.setup();
    renderSwitcher();
    await user.click(trigger());
    const listbox = screen.getByRole("listbox");
    await user.keyboard("ka");
    expect(listbox).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: /Kaga Bay Bus/ }).id);
    await user.keyboard("{Enter}");
    expect(screen.getByTestId("pathname")).toHaveTextContent("/agencies/2/reports");
  });

  it("moves the active option with the arrow keys", async () => {
    const user = userEvent.setup();
    renderSwitcher();
    await user.click(trigger());
    const listbox = screen.getByRole("listbox");
    await user.keyboard("{ArrowDown}");
    expect(listbox).toHaveAttribute("aria-activedescendant", screen.getByRole("option", { name: /Kaga Bay Bus/ }).id);
  });

  it("closes on Escape and hands focus back to the trigger", async () => {
    const user = userEvent.setup();
    renderSwitcher();
    await user.click(trigger());
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(trigger()).toHaveFocus();
  });

  it("opens when the command palette asks for it", async () => {
    renderSwitcher();
    openAgencySwitcher();
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
  });

  it("renders a plain label, with no listbox, when there is only one agency", () => {
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [agencies[0]], isLoading: false } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={[FILTERED_PATH]}>
        <Routes>
          <Route path="/agencies/:agencyId/*" element={<AgencySwitcher onSwitch={vi.fn()} />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("Hokuriku Transit")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Hokuriku Transit/ })).toBeNull();
  });
});
