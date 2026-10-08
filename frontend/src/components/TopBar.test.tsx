import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import type { Agency, RouteSummaryResponse } from "../api/types";
import { formatDateTime } from "../utils/format";
import { COMMAND_PALETTE_OPEN_EVENT } from "./commandPaletteEvents";
import { TopBar } from "./TopBar";

function agency(agency_id: number, latest_data_date: string | null): Agency {
  return { agency_id, agency_name: `Agency ${agency_id}`, feed_url: "", static_url: null, latest_data_date };
}

function summary(latest_captured_at: string | null): RouteSummaryResponse {
  return { latest_captured_at, date: null, routes: [], raw_samples: 436691, clamp_count: 0 };
}

function renderBar(path = "/agencies/9/pulse", latestCapturedAt: string | null = "2026-09-30T11:41:00Z") {
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data: [agency(1, "2026-08-01"), agency(9, "2026-09-29")],
    isLoading: false,
  } as never);
  vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: summary(latestCapturedAt) } as never);
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

  it.each([
    ["MacIntel", "⌘"],
    ["Win32", "Ctrl"],
  ])("names the shortcut's modifier for the platform (%s → %s)", (platform, modifier) => {
    vi.spyOn(navigator, "platform", "get").mockReturnValue(platform);
    renderBar();
    expect(screen.getByText(modifier)).toBeInTheDocument();
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

  describe("data freshness", () => {
    beforeEach(() => {
      vi.useFakeTimers({ toFake: ["Date"] });
      vi.setSystemTime(new Date("2026-09-30T12:00:00Z")); // 21:00 JST
    });
    afterEach(() => vi.useRealTimers());

    it("says in one place how far the analysis runs and when the feed last reported", () => {
      renderBar();
      const time = formatDateTime("2026-09-30T11:41:00Z", { timeStyle: "short" });
      expect(screen.getByRole("button", { name: `Analyzed through Tue, Sep 29 · live ${time}` })).toBeInTheDocument();
    });

    it("dates the last reading when it did not arrive today", () => {
      renderBar("/agencies/9/pulse", "2026-09-28T11:41:00Z");
      const when = formatDateTime("2026-09-28T11:41:00Z");
      expect(screen.getByRole("button", { name: `Analyzed through Tue, Sep 29 · last reading ${when}` })).toBeInTheDocument();
    });

    it("leaves the live part out before any reading has arrived", () => {
      renderBar("/agencies/9/pulse", null);
      expect(screen.getByRole("button", { name: "Analyzed through Tue, Sep 29" })).toBeInTheDocument();
    });

    it("spells it out in plain words on request, and closes on Escape", async () => {
      renderBar();
      const chip = screen.getByRole("button", { name: /Analyzed through/ });
      await userEvent.click(chip);
      const panel = screen.getByRole("region", { name: "How fresh the data is" });
      expect(panel).toHaveTextContent("Service days through Tue, Sep 29, 2026");
      expect(panel).toHaveTextContent("436,691");
      expect(panel).toHaveTextContent("Days without readings are left out, not counted as on time.");
      await userEvent.keyboard("{Escape}");
      expect(screen.queryByRole("region", { name: "How fresh the data is" })).toBeNull();
      expect(chip).toHaveFocus();
    });

    it("says the live reading is unknown, not missing, when the status could not be fetched", async () => {
      renderBar();
      vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: undefined, error: new Error("503") } as never);
      await userEvent.click(screen.getByRole("button", { name: /Analyzed through/ }));
      const panel = screen.getByRole("region", { name: "How fresh the data is" });
      expect(panel).not.toHaveTextContent("No readings received yet");
    });

    it("closes when the reader clicks elsewhere, leaving focus where the click put it", async () => {
      renderBar();
      const chip = screen.getByRole("button", { name: /Analyzed through/ });
      await userEvent.click(chip);
      await userEvent.click(document.body);
      expect(screen.queryByRole("region", { name: "How fresh the data is" })).toBeNull();
      expect(chip).not.toHaveFocus();
    });

    it("closes from its own chip as a toggle", async () => {
      renderBar();
      const chip = screen.getByRole("button", { name: /Analyzed through/ });
      await userEvent.click(chip);
      await userEvent.click(chip);
      expect(screen.queryByRole("region", { name: "How fresh the data is" })).toBeNull();
      expect(chip).toHaveAttribute("aria-expanded", "false");
    });

    it("states nothing about freshness while the agency has no data yet", () => {
      vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: [agency(9, null)], isLoading: false } as never);
      vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: summary(null) } as never);
      renderWithProviders(
        <MemoryRouter initialEntries={["/agencies/9/pulse"]}>
          <Routes>
            <Route path="/agencies/:agencyId/*" element={<TopBar />} />
          </Routes>
        </MemoryRouter>,
      );
      expect(screen.queryByRole("button", { name: /Analyzed through/ })).toBeNull();
    });
  });
});
