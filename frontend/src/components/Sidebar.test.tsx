import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import i18n from "../i18n";
import { Sidebar } from "./Sidebar";
import { readLastAgency, writeLastAgency } from "../api/lastAgency";

function renderSidebar(path = "/agencies/1/map") {
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
  it("renders the 5 main nav items with their label and subtitle", () => {
    renderSidebar();
    expect(screen.getByText("Overview")).toBeTruthy();
    expect(screen.getByText("What's happening right now")).toBeTruthy();
    expect(screen.getByText("Map")).toBeTruthy();
    expect(screen.getByText("Where it's happening")).toBeTruthy();
    expect(screen.getByText("Analysis")).toBeTruthy();
    expect(screen.getByText("When and why delays happen")).toBeTruthy();
    expect(screen.getByText("Agencies")).toBeTruthy();
    expect(screen.getByText("How you compare to others")).toBeTruthy();
    expect(screen.getByText("Latest observations")).toBeTruthy();
    expect(screen.getByText("Stop-by-stop readings")).toBeTruthy();
  });

  it("renders Ask as a distinct CTA", () => {
    renderSidebar();
    expect(screen.getByText("Ask")).toBeTruthy();
  });

  it("renders Live as a nav item when viewing a specific agency", () => {
    renderSidebar("/agencies/8/overview");
    expect(screen.getByRole("link", { name: /Latest observations/ })).toBeTruthy();
  });

  it("points the Live nav item at the current agency's live route, preserving the filter query string", () => {
    renderSidebar("/agencies/8/overview?from=2026-06-01&to=2026-06-07");
    const link = screen.getByRole("link", { name: /Latest observations/ });
    expect(link).toHaveAttribute("href", "/agencies/8/live?from=2026-06-01&to=2026-06-07");
  });

  it("does not render the Live nav item outside any agency context", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nextProvider i18n={i18n}>
          <MemoryRouter initialEntries={["/"]}>
            <Sidebar />
          </MemoryRouter>
        </I18nextProvider>
      </QueryClientProvider>
    );
    expect(screen.queryByRole("link", { name: /Latest observations/ })).toBeNull();
  });

  it("marks the current route's nav link as active", () => {
    renderSidebar("/agencies/1/map");
    const mapLink = screen.getByRole("link", { name: /Map/ });
    expect(mapLink.getAttribute("aria-current")).toBe("page");
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
    expect(screen.queryByText("Overview")).toBeNull();
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
    renderSidebar("/agencies/8/map");
    const link = screen.getByRole("link", { name: "No-data state" });
    expect(link).toHaveAttribute("href", "/agencies/8/overview?from=2030-01-01&to=2030-01-07");
  });

  it("points the feed-stale prototype link at the current agency's overview, preserving the active filter", () => {
    renderSidebar("/agencies/8/map?from=2026-06-01&to=2026-06-07");
    const link = screen.getByRole("link", { name: "Feed-stale state" });
    expect(link).toHaveAttribute("href", "/agencies/8/overview?from=2026-06-01&to=2026-06-07");
  });

  describe("collapse", () => {
    beforeEach(() => localStorage.clear());

    it("hides nav labels/subtitles and the PROTOTYPE section, but keeps the nav links, after collapsing", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      expect(screen.queryByText("Overview")).toBeNull();
      expect(screen.queryByText("What's happening right now")).toBeNull();
      expect(screen.queryByText("PROTOTYPE")).toBeNull();
      expect(screen.getByRole("link", { name: "Map" })).toBeTruthy();
    });

    it("shows an expand toggle once collapsed, which restores the labels when clicked", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
      expect(screen.getByText("Overview")).toBeTruthy();
    });

    it("persists the collapsed state to localStorage and restores it on remount", () => {
      localStorage.setItem("transit.sidebarCollapsed", "1");
      renderSidebar();
      expect(screen.queryByText("Overview")).toBeNull();
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
});
