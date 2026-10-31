import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { FeedHealthBanner } from "./FeedHealthBanner";
import * as hooks from "../api/hooks";
import type { RouteSummaryResponse } from "../api/types";

function summary(over: Partial<RouteSummaryResponse>): RouteSummaryResponse {
  return {
    latest_captured_at: "2026-06-09T10:00:00Z",
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
        <Route path="/:agencyId" element={<FeedHealthBanner />} />
      </Routes>
    </MemoryRouter>,
  );
}

// Mirrors Sidebar.test.tsx's mockMatchMedia helper, at the shared
// max-width:640px phone breakpoint.
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

describe("FeedHealthBanner", () => {
  afterEach(() => vi.restoreAllMocks());

  it("says how many readings were left out once they reach a thousandth of the readings", () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ raw_samples: 1_011_299, clamp_count: 2_321 }),
    } as never);
    renderBanner();
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent(
      "2,321 implausible readings are left out of the figures. The figures shown already exclude them.",
    );
    // scripts/capture_user_manual_screenshots.py hides the shell banners by this attribute.
    expect(banner).toHaveAttribute("data-shell-banner");
  });

  it("shows at exactly a thousandth", () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ raw_samples: 100_000, clamp_count: 100 }),
    } as never);
    renderBanner();
    expect(screen.getByRole("status")).toHaveTextContent("100 implausible readings");
  });

  it("renders nothing below a thousandth, leaving the count to the freshness panel", () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ raw_samples: 19_345_155, clamp_count: 112 }),
    } as never);
    renderBanner();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("prints the whole message on a narrow viewport, with the dismiss button its only control", () => {
    mockMatchMedia(true);
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ raw_samples: 1_000, clamp_count: 5 }),
    } as never);
    renderBanner();
    expect(screen.getAllByRole("button")).toEqual([screen.getByRole("button", { name: "Dismiss" })]);
    const message = screen.getByText(/5 implausible readings/);
    expect(message.style.textOverflow).toBe("");
    expect(message.style.whiteSpace).toBe("");
  });

  it("renders nothing when clamp_count is 0 (healthy feed)", () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ clamp_count: 0 }),
    } as never);
    renderBanner();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("hides after the user dismisses it", async () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ clamp_count: 5 }),
    } as never);
    renderBanner();
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("FeedHealthBanner dismissal", () => {
  function renderSwitchable() {
    renderWithProviders(
      <MemoryRouter initialEntries={["/9"]}>
        <Routes>
          <Route
            path="/:agencyId"
            element={<><FeedHealthBanner /><Link to="/10">other agency</Link><Link to="/9">first agency</Link></>}
          />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("keeps a dismissal to the agency it was dismissed for", async () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: summary({ clamp_count: 5 }) } as never);
    renderSwitchable();
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByRole("status")).toBeNull();
    await userEvent.click(screen.getByRole("link", { name: "other agency" }));
    expect(screen.getByRole("status")).toBeTruthy();
    await userEvent.click(screen.getByRole("link", { name: "first agency" }));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows again once the page is loaded afresh", async () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: summary({ clamp_count: 5 }) } as never);
    renderBanner();
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByRole("status")).toBeNull();
    cleanup();
    renderBanner();
    expect(screen.getByRole("status")).toBeTruthy();
  });
});
