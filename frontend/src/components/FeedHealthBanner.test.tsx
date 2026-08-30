import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
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

beforeEach(() => {
  sessionStorage.clear();
});

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

describe("FeedHealthBanner", () => {
  it("shows the count when clamp_count > 0", () => {
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ clamp_count: 1906 }),
    } as never);
    renderBanner();
    const banner = screen.getByRole("status");
    expect(banner.textContent).toContain("1906");
  });

  it("renders the message as a single tappable line on a narrow viewport", () => {
    mockMatchMedia(true);
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: summary({ clamp_count: 5 }),
    } as never);
    renderBanner();
    // The message itself becomes a second, distinctly-named "button" (tap to
    // expand) alongside the existing dismiss "×" button — getByRole with a
    // name scopes to it specifically, so this fails loudly if the dismiss
    // button's own accessible name ever collided with the message text.
    const messageButton = screen.getByRole("button", { name: /5/ });
    expect(messageButton).toHaveAttribute("aria-expanded", "false");
    vi.restoreAllMocks();
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
