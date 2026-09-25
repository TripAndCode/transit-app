import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import i18n from "../i18n";
import { Sidebar } from "./Sidebar";
import { readLastAgency, writeLastAgency } from "../api/lastAgency";

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

function renderSidebar(path = "/agencies/1/operations") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/agencies/:agencyId/*" element={<Sidebar />} />
          </Routes>
        </MemoryRouter>
      </I18nextProvider>
    </QueryClientProvider>
  );
}

describe("Sidebar", () => {
  it("renders the five nav destinations, including Period overview and Network", () => {
    renderSidebar();
    expect(screen.getByText("Operations")).toBeTruthy();
    expect(screen.getByText("Period overview")).toBeTruthy();
    expect(screen.getByText("Segment analysis")).toBeTruthy();
    expect(screen.getByText("Compare agencies")).toBeTruthy();
    expect(screen.getByText("Reports")).toBeTruthy();
    expect(screen.queryByText("Agencies")).toBeNull();
    expect(screen.queryByText("Latest observations")).toBeNull();
  });

  it("renders Ask as a distinct CTA", () => {
    renderSidebar();
    expect(screen.getByText("Ask")).toBeTruthy();
  });

  it("folds the former Live view into Operations", () => {
    renderSidebar("/agencies/8/operations");
    expect(screen.getByRole("link", { name: "Operations" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Latest observations/ })).toBeNull();
  });

  it("points Operations at the current agency's operations route, preserving the filter query string", () => {
    renderSidebar("/agencies/8/operations?from=2026-06-01&to=2026-06-07");
    const link = screen.getByRole("link", { name: "Operations" });
    expect(link).toHaveAttribute("href", "/agencies/8/operations?from=2026-06-01&to=2026-06-07");
  });

  it("points Period overview at the agency's period-overview route, preserving the filter query string", () => {
    renderSidebar("/agencies/8/operations?from=2026-06-01&to=2026-06-07");
    const link = screen.getByRole("link", { name: "Period overview" });
    expect(link).toHaveAttribute("href", "/agencies/8/period-overview?from=2026-06-01&to=2026-06-07");
  });

  it("points Network at the agency's network route", () => {
    renderSidebar("/agencies/8/operations");
    const link = screen.getByRole("link", { name: "Compare agencies" });
    expect(link.getAttribute("href")).toMatch(/^\/agencies\/8\/network/);
  });

  it("does not render Operations outside any agency context", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nextProvider i18n={i18n}>
          <MemoryRouter initialEntries={["/"]}>
            <Sidebar />
          </MemoryRouter>
        </I18nextProvider>
      </QueryClientProvider>
    );
    expect(screen.queryByRole("link", { name: /Operations/ })).toBeNull();
  });

  it("marks the current route's nav link as active", () => {
    renderSidebar("/agencies/1/operations");
    const mapLink = screen.getByRole("link", { name: "Operations" });
    expect(mapLink.getAttribute("aria-current")).toBe("page");
  });

  it("renders a command-palette hint in the footer that opens the palette", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    window.addEventListener("command-palette:open", onOpen);
    renderSidebar();
    await user.click(screen.getByRole("button", { name: /Open the command palette/ }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    window.removeEventListener("command-palette:open", onOpen);
  });

  it("hides the command-palette hint while collapsed", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(screen.queryByRole("button", { name: /Open the command palette/ })).toBeNull();
    // Collapsing persists the preference to localStorage (see the "collapse"
    // describe block below); reset it so later tests in this file don't
    // inherit a collapsed sidebar.
    localStorage.clear();
  });

  it("renders the brand block above the nav items", () => {
    renderSidebar();
    expect(screen.getByText("Delay Dashboard")).toBeTruthy();
    expect(screen.getByText("Real-time × Timetable")).toBeTruthy();
  });

  it("renders the brand block even when there is no agencyId, but not the nav items", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nextProvider i18n={i18n}>
          <MemoryRouter initialEntries={["/"]}>
            <Sidebar />
          </MemoryRouter>
        </I18nextProvider>
      </QueryClientProvider>
    );
    expect(screen.getByText("Delay Dashboard")).toBeTruthy();
    expect(screen.getByText("Real-time × Timetable")).toBeTruthy();
    expect(screen.queryByText("Operations")).toBeNull();
  });

  it("renders the dev-only PROTOTYPE section with all three state links", () => {
    renderSidebar();
    expect(screen.getByText("PROTOTYPE")).toBeTruthy();
    expect(screen.getByText("First-time login screen")).toBeTruthy();
    expect(screen.getByText("Feed-stale state")).toBeTruthy();
    expect(screen.getByText("No-data state")).toBeTruthy();
  });

  it("clears the remembered agency and navigates to / when the onboarding prototype link is clicked", async () => {
    const user = userEvent.setup();
    writeLastAgency(1);
    renderSidebar();
    await user.click(screen.getByText("First-time login screen"));
    expect(readLastAgency()).toBeNull();
  });

  it("points the no-data prototype link at a far-future date range on the current agency", () => {
    renderSidebar("/agencies/8/operations");
    const link = screen.getByRole("link", { name: "No-data state" });
    expect(link).toHaveAttribute("href", "/agencies/8/period-overview?from=2030-01-01&to=2030-01-07");
  });

  it("points the feed-stale prototype link at the current agency's operations view, preserving the active filter", () => {
    renderSidebar("/agencies/8/operations?from=2026-06-01&to=2026-06-07");
    const link = screen.getByRole("link", { name: "Feed-stale state" });
    expect(link).toHaveAttribute("href", "/agencies/8/operations?from=2026-06-01&to=2026-06-07");
  });

  describe("collapse", () => {
    beforeEach(() => localStorage.clear());

    it("hides nav labels/subtitles and the PROTOTYPE section, but keeps the nav links, after collapsing", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      expect(screen.queryByText("Operations")).toBeNull();
      expect(screen.queryByText("What's happening right now")).toBeNull();
      expect(screen.queryByText("PROTOTYPE")).toBeNull();
      expect(screen.getByRole("link", { name: "Operations" })).toBeTruthy();
    });

    it("shows an expand toggle once collapsed, which restores the labels when clicked", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
      expect(screen.getByText("Operations")).toBeTruthy();
    });

    it("persists the collapsed state to localStorage and restores it on remount", () => {
      localStorage.setItem("transit.sidebarCollapsed", "1");
      renderSidebar();
      expect(screen.queryByText("Operations")).toBeNull();
      expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeTruthy();
    });

    it("hides the account-menu trigger while collapsed", async () => {
      const user = userEvent.setup();
      renderSidebar();
      expect(await screen.findByRole("button", { name: "Account menu" })).toBeTruthy();
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      expect(screen.queryByRole("button", { name: "Account menu" })).toBeNull();
    });
  });

  it("renders the account-menu trigger (agency-independent) at the bottom of the sidebar", async () => {
    // A prior test in this file (the "collapse" describe) persists the
    // collapsed preference to localStorage; clear it so this test starts
    // from the expanded state regardless of run order.
    localStorage.clear();
    renderSidebar();
    expect(await screen.findByRole("button", { name: "Account menu" })).toBeTruthy();
  });

  describe("desktop/mobile split", () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });

    it("renders only the desktop rail (no mobile trigger) on a wide viewport", () => {
      mockMatchMedia(false);
      renderSidebar();
      // Previously both the desktop <aside> and the mobile rail+trigger
      // mounted unconditionally (toggled only via a CSS display media
      // query), so the mobile trigger existed in the DOM at every viewport
      // width. Conditionally rendering on isMobile means it's now absent
      // entirely on a wide viewport.
      expect(screen.queryByRole("button", { name: "Open menu" })).toBeNull();
      expect(screen.getAllByRole("link", { name: /Operations/ }).length).toBe(1);
    });

    it("renders the bottom tab bar (no desktop rail) on a narrow viewport", () => {
      mockMatchMedia(true);
      renderSidebar();
      expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeTruthy();
      // The desktop rail's own collapse toggle is the clearest sign the
      // desktop variant isn't also mounted underneath.
      expect(screen.queryByRole("button", { name: "Collapse sidebar" })).toBeNull();
    });
  });

  describe("mobile tab bar", () => {
    beforeEach(() => {
      mockMatchMedia(true);
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it("renders the four destinations (three tabs plus Ask) as labelled links", () => {
      renderSidebar();
      const nav = screen.getByRole("navigation", { name: "Primary navigation" });
      expect(within(nav).getByRole("link", { name: /Operations/ })).toBeTruthy();
      expect(within(nav).getByRole("link", { name: /Segment analysis/ })).toBeTruthy();
      expect(within(nav).getByRole("link", { name: /Reports/ })).toBeTruthy();
      expect(within(nav).getByRole("link", { name: /Ask/ })).toBeTruthy();
    });

    it("marks the active tab", () => {
      // `/overview` only redirects to `operations`, so a tab never matches it.
      renderSidebar("/agencies/1/operations");
      const nav = screen.getByRole("navigation", { name: "Primary navigation" });
      expect(within(nav).getByRole("link", { name: /Operations/ })).toHaveAttribute("aria-current", "page");
    });

    it("does not render the four destinations outside any agency context", () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <I18nextProvider i18n={i18n}>
            <MemoryRouter initialEntries={["/"]}>
              <Sidebar />
            </MemoryRouter>
          </I18nextProvider>
        </QueryClientProvider>
      );
      expect(screen.queryByRole("link", { name: /Operations/ })).toBeNull();
      expect(screen.getByRole("button", { name: "More" })).toBeTruthy();
    });

    it("renders a More trigger that does not mount the agency picker until opened", () => {
      renderSidebar();
      expect(screen.getByRole("button", { name: "More" })).toBeTruthy();
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("stacks under the sheet's backdrop, so an aria-modal sheet is genuinely modal", async () => {
      const user = userEvent.setup();
      renderSidebar();
      const nav = screen.getByRole("navigation", { name: "Primary navigation" });
      await user.click(screen.getByRole("button", { name: "More" }));

      const backdrop = Number(screen.getByRole("presentation").style.zIndex);
      // A tab bar above the backdrop stays tappable while the sheet claims
      // `aria-modal`, and the route change it causes leaves the backdrop and
      // the focus trap mounted over the page that replaced it.
      expect(Number(nav.style.zIndex)).toBeLessThan(backdrop);
    });
  });

  describe("mobile more sheet", () => {
    beforeEach(() => {
      mockMatchMedia(true);
    });

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it("opens a dialog with the brand block and account menu, but not the four tab destinations again", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "More" }));
      const dialog = screen.getByRole("dialog");
      expect(within(dialog).getByText("Delay Dashboard")).toBeTruthy();
      expect(await within(dialog).findByRole("button", { name: "Account menu" })).toBeTruthy();
      // The nav destinations already live in the tab bar underneath; the
      // sheet must not repeat them.
      expect(within(dialog).queryByRole("link", { name: /Operations/ })).toBeNull();
    });

    it("closes when the close button inside it is clicked", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "More" }));
      expect(screen.getByRole("dialog")).toBeTruthy();
      await user.click(screen.getByRole("button", { name: "Close menu" }));
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("renders through the shared overlay base", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "More" }));
      expect(screen.getByRole("dialog")).toHaveClass("ui-overlay-panel");
      expect(screen.getByRole("presentation")).toHaveClass("ui-overlay-scrim");
    });

    it("closes when the backdrop is clicked", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "More" }));
      expect(screen.getByRole("dialog")).toBeTruthy();
      await user.click(screen.getByRole("presentation"));
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("closes when Escape is pressed", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "More" }));
      expect(screen.getByRole("dialog")).toBeTruthy();
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("closes when a nav link inside it (e.g. the onboarding prototype link) is clicked", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "More" }));
      await user.click(screen.getByText("First-time login screen"));
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });
});
