import { describe, it, expect, vi, afterEach } from "vitest";
import { act, cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { DataStalenessBanner } from "./DataStalenessBanner";
import * as hooks from "../api/hooks";
import type { RouteSummaryResponse } from "../api/types";

function summary(over: Partial<RouteSummaryResponse>): RouteSummaryResponse {
  return {
    latest_captured_at: new Date().toISOString(),
    date: "2026-06-09",
    routes: [],
    raw_samples: 100,
    clamp_count: 0,
    ...over,
  };
}

function renderBanner() {
  renderWithProviders(
    <MemoryRouter initialEntries={["/9"]}>
      <Routes>
        <Route path="/:agencyId" element={<DataStalenessBanner />} />
      </Routes>
    </MemoryRouter>,
  );
}

function hoursAgoIso(hours: number): string {
  return new Date(Date.now() - hours * 60 * 60 * 1000).toISOString();
}

// Mirrors Sidebar.test.tsx's mockMatchMedia helper — same shared
// max-width:640px query used by useTapToExpandBanner.
function mockMatchMedia(matches: boolean) {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches,
    media: "(max-width: 640px)",
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as unknown as MediaQueryList);
}

describe("DataStalenessBanner", () => {
  it("shows a warning when the latest observation is stale (> 24h old)", () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ latest_captured_at: hoursAgoIso(48) }),
    } as never);
    renderBanner();
    // scripts/capture_user_manual_screenshots.py hides the shell banners by this attribute.
    expect(screen.getByRole("status")).toHaveAttribute("data-shell-banner");
  });

  it("renders nothing when the latest observation is recent", () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ latest_captured_at: hoursAgoIso(1) }),
    } as never);
    renderBanner();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("hides after the user dismisses it", async () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ latest_captured_at: hoursAgoIso(48) }),
    } as never);
    renderBanner();
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders the message as a single tappable line on a narrow viewport", () => {
    mockMatchMedia(true);
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ latest_captured_at: hoursAgoIso(48) }),
    } as never);
    renderBanner();
    const buttons = screen.getAllByRole("button");
    const messageButton = buttons.find((b) => b.getAttribute("aria-expanded") === "false");
    expect(messageButton).toBeDefined();
    vi.restoreAllMocks();
  });
});

describe("DataStalenessBanner dismissal", () => {
  function renderSwitchable() {
    renderWithProviders(
      <MemoryRouter initialEntries={["/9"]}>
        <Routes>
          <Route
            path="/:agencyId"
            element={<><DataStalenessBanner /><Link to="/10">other agency</Link><Link to="/9">first agency</Link></>}
          />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("keeps a dismissal to the agency it was dismissed for", async () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: summary({ latest_captured_at: hoursAgoIso(48) }) } as never);
    renderSwitchable();
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByRole("status")).toBeNull();
    await userEvent.click(screen.getByRole("link", { name: "other agency" }));
    expect(screen.getByRole("status")).toBeTruthy();
    await userEvent.click(screen.getByRole("link", { name: "first agency" }));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows again once the page is loaded afresh", async () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: summary({ latest_captured_at: hoursAgoIso(48) }) } as never);
    renderBanner();
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByRole("status")).toBeNull();
    cleanup();
    renderBanner();
    expect(screen.getByRole("status")).toBeTruthy();
  });
});

describe("DataStalenessBanner on a page left open", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("warns once the feed's age crosses the threshold, without a reload", () => {
    vi.useFakeTimers();
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ latest_captured_at: hoursAgoIso(23.5) }),
    } as never);
    renderBanner();
    expect(screen.queryByRole("status")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(60 * 60 * 1000);
    });
    expect(screen.getByRole("status")).toBeTruthy();
  });
});
