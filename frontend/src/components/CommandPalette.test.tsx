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

  it("renders through the shared overlay base", () => {
    renderPalette();
    openWithCtrlK();
    expect(screen.getByRole("dialog")).toHaveClass("ui-overlay-panel", "cmdp-palette");
    expect(screen.getByRole("presentation")).toHaveClass("ui-overlay-scrim", "cmdp-overlay");
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
    await user.type(input, "Pulse");
    expect(screen.getByText("Pulse")).toBeInTheDocument();
    expect(screen.queryByText("Live")).toBeNull();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/pulse");
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
    it("navigates to live on g then l when nothing is focused in a text field", () => {
      renderPalette();
      fireEvent.keyDown(document, { key: "g" });
      fireEvent.keyDown(document, { key: "l" });
      expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/live");
    });

    it.each([
      ["p", "pulse"],
      ["r", "routes"],
      ["t", "time"],
      ["w", "why"],
      ["c", "compare"],
      ["e", "reports"],
      ["q", "ask"],
    ])("navigates on g then %s to %s", (key, dest) => {
      renderPalette();
      fireEvent.keyDown(document, { key: "g" });
      fireEvent.keyDown(document, { key });
      expect(screen.getByTestId("pathname").textContent).toBe(`/agencies/1/${dest}`);
    });

    it("ignores the chord while focus is in a text input", () => {
      renderPalette("/agencies/1/overview", <input data-testid="outside-input" />);
      const outside = screen.getByTestId("outside-input");
      fireEvent.keyDown(outside, { key: "g" });
      fireEvent.keyDown(outside, { key: "l" });
      expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/overview");
    });

    it("does not fire on a bare 'l' without a preceding 'g'", () => {
      renderPalette();
      fireEvent.keyDown(document, { key: "l" });
      expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/overview");
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("opens the shortcut sheet on '?' and lists the go-to chords for every destination plus Ask", () => {
    renderPalette();
    fireEvent.keyDown(document, { key: "?" });
    const dialog = screen.getByRole("dialog", { name: "Keyboard shortcuts" });
    for (const name of ["Pulse", "Routes", "Time", "Why", "Compare", "Live", "Reports", "Ask"]) {
      expect(within(dialog).getByText(`Go to ${name}`)).toBeInTheDocument();
    }
    expect(within(dialog).getByText("Show this shortcut list")).toBeInTheDocument();
  });

  it("closes the shortcut sheet on Escape", () => {
    renderPalette();
    fireEvent.keyDown(document, { key: "?" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("selecting a route opens its dossier with the active scope", async () => {
    const user = userEvent.setup();
    renderPalette("/agencies/1/live?from=2026-06-01&to=2026-06-07&routes=7");
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "42");
    await user.click(screen.getByText("42 (42)"));
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/routes/42");
    const search = new URLSearchParams(screen.getByTestId("search").textContent ?? "");
    expect(search.get("from")).toBe("2026-06-01");
    expect(search.has("routes")).toBe(false);
  });

  it("names the screen a report opens on", async () => {
    const user = userEvent.setup();
    renderPalette();
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Dwell");
    const option = screen.getByText("Dwell/running time").closest('[role="option"]') as HTMLElement;
    expect(within(option).getByText("Why")).toBeInTheDocument();
    expect(option.textContent).not.toContain("analysis/");
  });

  it("selecting a report opens the screen that hosts it", async () => {
    const user = userEvent.setup();
    renderPalette("/agencies/1/live?from=2026-06-01&to=2026-06-07");
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Dwell");
    await user.click(screen.getByText("Dwell/running time"));
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/1/why");
    const search = new URLSearchParams(screen.getByTestId("search").textContent ?? "");
    expect(search.get("report")).toBe("dwell_run");
    expect(search.get("from")).toBe("2026-06-01");
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

  it("switching agencies keeps the current destination and the active range context", async () => {
    const user = userEvent.setup();
    renderPalette("/agencies/1/time?from=2026-06-01&to=2026-06-07");
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Kaga Bay Bus");
    await user.click(screen.getByText("Kaga Bay Bus"));
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/2/time");
    expect(screen.getByTestId("search").textContent).toBe("?from=2026-06-01&to=2026-06-07");
  });

  it("switching agencies on the agencies board stays on the board", async () => {
    const user = userEvent.setup();
    renderPalette("/agencies/1/compare?by=agencies&from=2026-06-01&to=2026-06-07");
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Kaga Bay Bus");
    await user.click(screen.getByText("Kaga Bay Bus"));
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/2/compare");
    const search = new URLSearchParams(screen.getByTestId("search").textContent ?? "");
    expect(search.get("by")).toBe("agencies");
    expect(search.get("from")).toBe("2026-06-01");
  });

  it("switching agencies from a route dossier lands on the Routes list with the active range context", async () => {
    const user = userEvent.setup();
    renderPalette("/agencies/1/routes/42?routes=42&from=2026-06-01&to=2026-06-07");
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Kaga Bay Bus");
    await user.click(screen.getByText("Kaga Bay Bus"));
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/2/routes");
    const search = new URLSearchParams(screen.getByTestId("search").textContent ?? "");
    expect(search.get("from")).toBe("2026-06-01");
    expect(search.has("routes")).toBe(false);
  });

  it("switching agencies falls back to Pulse, with the active range context, when there is no current tab", async () => {
    const user = userEvent.setup();
    renderPalette("/agencies/1?from=2026-06-01&to=2026-06-07");
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Kaga Bay Bus");
    await user.click(screen.getByText("Kaga Bay Bus"));
    expect(screen.getByTestId("pathname").textContent).toBe("/agencies/2/pulse");
    expect(screen.getByTestId("search").textContent).toBe("?from=2026-06-01&to=2026-06-07");
  });

  it("records a run item in localStorage and shows it under Recent next time", async () => {
    const user = userEvent.setup();
    renderPalette();
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Pulse");
    await user.click(screen.getByText("Pulse"));

    const stored = JSON.parse(localStorage.getItem("transit.commandPaletteRecents") ?? "[]");
    expect(stored).toContain("nav:pulse");

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
    await user.type(screen.getByRole("combobox"), "Pulse");
    await user.click(screen.getByText("Pulse"));

    const stored: string[] = JSON.parse(localStorage.getItem("transit.commandPaletteRecents") ?? "[]");
    expect(stored.length).toBe(8);
    expect(stored[0]).toBe("nav:pulse");
    expect(stored).not.toContain("action:theme");
  });

  it("does not crash when localStorage throws (private browsing / quota)", async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked");
    });
    renderPalette();
    openWithCtrlK();
    await user.type(screen.getByRole("combobox"), "Pulse");
    await expect(user.click(screen.getByText("Pulse"))).resolves.not.toThrow();
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
