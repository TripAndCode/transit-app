import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { CommandPalette } from "./CommandPalette";
import * as hooks from "../api/hooks";
import type { Agency, Route as ApiRoute } from "../api/types";

const agencies: Agency[] = [
  { agency_id: 1, agency_name: "Hokuriku Transit", feed_url: "", static_url: null, latest_data_date: null },
  { agency_id: 2, agency_name: "Kaga Bay Bus", feed_url: "", static_url: null, latest_data_date: null },
];

const routes: ApiRoute[] = [
  { route_id: "r42", route_short_name: "42", route_long_name: "Port Line", route_code: "42", trip_headsigns: [] },
];

function Probe() {
  const location = useLocation();
  return (
    <div>
      <div data-testid="pathname">{location.pathname}</div>
      <div data-testid="search">{location.search}</div>
    </div>
  );
}

function renderPalette(initialPath = "/agencies/1/overview", extra?: React.ReactNode) {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: agencies, isPending: false } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: routes, isPending: false } as never);
  return renderWithProviders(
    <MemoryRouter initialEntries={[initialPath]}>
      <CommandPalette />
      <Probe />
      {extra}
    </MemoryRouter>,
  );
}

function openWithCtrlK() {
  fireEvent.keyDown(document, { key: "k", ctrlKey: true });
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("CommandPalette", () => {
  it("renders nothing until opened", () => {
    renderPalette();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens on Ctrl+K / Cmd+K", () => {
    renderPalette();
    openWithCtrlK();
    expect(screen.getByRole("dialog", { name: "Command palette" })).toBeInTheDocument();
  });

  it("ignores Ctrl+K while focus is in a text input elsewhere on the page", () => {
    renderPalette("/agencies/1/overview", <input data-testid="outside-input" />);
    const outside = screen.getByTestId("outside-input");
    fireEvent.keyDown(outside, { key: "k", ctrlKey: true });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("filters the list as the user types and navigates on Enter", async () => {
    const user = userEvent.setup();
    renderPalette();
    openWithCtrlK();
    const input = screen.getByRole("combobox");
    await user.type(input, "Reports");
    expect(screen.getByText("Reports")).toBeInTheDocument();
    expect(screen.queryByText("Segment analysis")).toBeNull();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/reports");
  });

  it("shows no-results copy when nothing matches", async () => {
    const user = userEvent.setup();
    renderPalette();
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "zzzznomatch");
    expect(screen.getByText("No matching items")).toBeInTheDocument();
  });

  it("closes on Escape and restores focus to whatever was focused before opening", () => {
    renderPalette("/agencies/1/overview", <button>trigger</button>);
    const trigger = screen.getByText("trigger");
    trigger.focus();
    expect(document.activeElement).toBe(trigger);

    openWithCtrlK();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByRole("combobox"));

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  describe("go-to chords", () => {
    it("navigates on g then o/a/r/q when nothing is focused in a text field", () => {
      renderPalette();
      fireEvent.keyDown(document, { key: "g" });
      fireEvent.keyDown(document, { key: "o" });
      // `/overview` is only a legacy alias that redirects to `operations`,
      // so the chord must not route through it.
      expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/operations");
    });

    it("navigates to route-analysis on g then a", () => {
      renderPalette();
      fireEvent.keyDown(document, { key: "g" });
      fireEvent.keyDown(document, { key: "a" });
      expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/route-analysis");
    });

    it("navigates to period-overview on g then p", () => {
      renderPalette();
      fireEvent.keyDown(document, { key: "g" });
      fireEvent.keyDown(document, { key: "p" });
      expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/period-overview");
    });

    it("navigates to network on g then n", () => {
      renderPalette();
      fireEvent.keyDown(document, { key: "g" });
      fireEvent.keyDown(document, { key: "n" });
      expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/network");
    });

    it("ignores the chord while focus is in a text input", () => {
      renderPalette("/agencies/1/overview", <input data-testid="outside-input" />);
      const outside = screen.getByTestId("outside-input");
      fireEvent.keyDown(outside, { key: "g" });
      fireEvent.keyDown(outside, { key: "o" });
      expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/overview");
    });

    it("does not fire on a bare 'o' without a preceding 'g'", () => {
      renderPalette();
      fireEvent.keyDown(document, { key: "o" });
      expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/overview");
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("opens the shortcut sheet on '?' and lists the go-to chords for every sidebar destination plus Ask", () => {
    renderPalette();
    fireEvent.keyDown(document, { key: "?" });
    const dialog = screen.getByRole("dialog", { name: "Keyboard shortcuts" });
    expect(within(dialog).getByText("Go to Operations")).toBeInTheDocument();
    expect(within(dialog).getByText("Go to Period overview")).toBeInTheDocument();
    expect(within(dialog).getByText("Go to Segment analysis")).toBeInTheDocument();
    expect(within(dialog).getByText("Go to Compare agencies")).toBeInTheDocument();
    expect(within(dialog).getByText("Go to Reports")).toBeInTheDocument();
    expect(within(dialog).getByText("Go to Ask")).toBeInTheDocument();
    expect(within(dialog).getByText("Show this shortcut list")).toBeInTheDocument();
  });

  it("closes the shortcut sheet on Escape", () => {
    renderPalette();
    fireEvent.keyDown(document, { key: "?" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("selecting a route sets the routes filter and jumps to segment analysis", async () => {
    const user = userEvent.setup();
    renderPalette();
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "42");
    await user.click(screen.getByText("42 (42)"));
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/route-analysis");
    expect(screen.getByTestId("search").textContent).toContain("routes=42");
  });

  it("selecting a time band updates the current page's query string", async () => {
    const user = userEvent.setup();
    renderPalette();
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Morning");
    await user.click(screen.getByText("Morning (05–09)"));
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/overview");
    expect(screen.getByTestId("search").textContent).toContain("time_band=morning");
  });

  it("switching agencies keeps the current tab and the active range context", async () => {
    const user = userEvent.setup();
    renderPalette("/agencies/1/route-analysis?from=2026-06-01&to=2026-06-07");
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Kaga Bay Bus");
    await user.click(screen.getByText("Kaga Bay Bus"));
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/2/route-analysis");
    expect(screen.getByTestId("search").textContent).toBe("?from=2026-06-01&to=2026-06-07");
  });

  it("switching agencies falls back to operations, with the active range context, when there is no current tab", async () => {
    const user = userEvent.setup();
    renderPalette("/agencies/1?from=2026-06-01&to=2026-06-07");
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Kaga Bay Bus");
    await user.click(screen.getByText("Kaga Bay Bus"));
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/2/operations");
    expect(screen.getByTestId("search").textContent).toBe("?from=2026-06-01&to=2026-06-07");
  });

  it("records a run item in localStorage and shows it under Recent next time", async () => {
    const user = userEvent.setup();
    renderPalette();
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Reports");
    await user.click(screen.getByText("Reports"));

    const stored = JSON.parse(localStorage.getItem("transit.commandPaletteRecents") ?? "[]");
    expect(stored).toContain("nav:reports");

    openWithCtrlK();
    expect(screen.getByText("Recent")).toBeInTheDocument();
  });

  it("caps recents at 8 entries, dropping the oldest", async () => {
    const user = userEvent.setup();
    localStorage.setItem(
      "transit.commandPaletteRecents",
      JSON.stringify(["timeband:morning", "timeband:forenoon", "timeband:noon", "timeband:afternoon", "timeband:evening", "timeband:night", "timeband:late_night", "action:theme"]),
    );
    renderPalette();
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Reports");
    await user.click(screen.getByText("Reports"));

    const stored: string[] = JSON.parse(localStorage.getItem("transit.commandPaletteRecents") ?? "[]");
    expect(stored.length).toBe(8);
    expect(stored[0]).toBe("nav:reports");
    expect(stored).not.toContain("action:theme");
  });

  it("does not crash when localStorage throws (private browsing / quota)", async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked");
    });
    renderPalette();
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Reports");
    await expect(user.click(screen.getByText("Reports"))).resolves.not.toThrow();
  });

  it("exposes each option's id via aria-activedescendant on the input, tracking arrow-key navigation", () => {
    renderPalette();
    openWithCtrlK();
    const input = screen.getByRole("combobox");
    const options = screen.getAllByRole("option");
    expect(options.length).toBeGreaterThan(1);
    expect(options[0]).toHaveAttribute("id");
    expect(input).toHaveAttribute("aria-activedescendant", options[0].id);

    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input).toHaveAttribute("aria-activedescendant", options[1].id);
  });

  it("marks the input as a list-autocomplete combobox", () => {
    renderPalette();
    openWithCtrlK();
    expect(screen.getByRole("combobox")).toHaveAttribute("aria-autocomplete", "list");
  });

  it("groups options under labelled ARIA groups instead of a plain heading", () => {
    renderPalette();
    openWithCtrlK();
    const groups = screen.getAllByRole("group");
    expect(groups.length).toBeGreaterThan(0);
    for (const group of groups) {
      expect(group).toHaveAttribute("aria-label");
    }
  });

  it("toggles the theme via the action group without navigating", async () => {
    const user = userEvent.setup();
    renderPalette();
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Toggle theme");
    await user.click(screen.getByText("Toggle theme"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/overview");
  });
});
