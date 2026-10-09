import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import i18n from "../i18n";
import { Sidebar } from "./Sidebar";
import { ToastProvider } from "./ui/Toast";
import { clearLastAgency, readLastAgency, writeLastAgency } from "../api/lastAgency";
import * as auth from "../api/auth";
import * as config from "../api/config";
import { rememberScreenScope } from "../api/screenScope";
import * as hooks from "../api/hooks";
import { togglePinnedRoute } from "../api/pinnedRoutes";

const RAIL_ORDER = ["Pulse", "Routes", "Time", "Why", "Compare", "Live", "Reports"];

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

function renderSidebar(path = "/agencies/1/live") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <ToastProvider>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/agencies/:agencyId/*" element={<Sidebar />} />
              <Route path="/help" element={<Sidebar />} />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </I18nextProvider>
    </QueryClientProvider>
  );
}

describe("Sidebar", () => {
  beforeEach(() => sessionStorage.clear());

  it("renders the seven destinations in rail order, with the palette left to the top bar", () => {
    renderSidebar();
    const nav = screen.getByRole("navigation", { name: "Destinations" });
    const links = within(nav).getAllByRole("link");
    expect(links).toHaveLength(RAIL_ORDER.length);
    expect(RAIL_ORDER.map((name) => links.indexOf(within(nav).getByRole("link", { name })))).toEqual(RAIL_ORDER.map((_, i) => i));
    expect(screen.queryByRole("button", { name: /Open the command palette/ })).toBeNull();
  });

  it("keeps its destinations on Help, leading back to the last chosen agency", () => {
    writeLastAgency(3);
    try {
      renderSidebar("/help");
      const nav = screen.getByRole("navigation", { name: "Destinations" });
      expect(within(nav).getByRole("link", { name: "Pulse" }).getAttribute("href")).toMatch(/^\/agencies\/3\/pulse/);
    } finally {
      clearLastAgency();
    }
  });

  describe("each screen keeps its own filters", () => {
    it("keeps a visible Ask entry below the destinations, without the current screen's filters", () => {
      renderSidebar("/agencies/8/live?from=2026-06-01&to=2026-06-07");
      expect(screen.getByRole("link", { name: "Ask" })).toHaveAttribute("href", "/agencies/8/ask");
    });

    it("points Live, the screen on show, at its own current filters", () => {
      renderSidebar("/agencies/8/live?from=2026-06-01&to=2026-06-07");
      expect(screen.getByRole("link", { name: "Live" })).toHaveAttribute("href", "/agencies/8/live?from=2026-06-01&to=2026-06-07");
    });

    it.each([
      ["Routes", "routes"],
      ["Time", "time"],
      ["Reports", "reports"],
    ])("points %s at the agency's %s screen without the current screen's filters", (name, dest) => {
      renderSidebar("/agencies/8/live?from=2026-06-01&to=2026-06-07");
      expect(screen.getByRole("link", { name })).toHaveAttribute("href", `/agencies/8/${dest}`);
    });

    it("reopens a screen with the filters it last showed", () => {
      rememberScreenScope("8", "routes", "routes=W54");
      renderSidebar("/agencies/8/live?dow=weekend");
      expect(screen.getByRole("link", { name: "Routes" })).toHaveAttribute("href", "/agencies/8/routes?routes=W54");
      expect(screen.getByRole("link", { name: "Time" })).toHaveAttribute("href", "/agencies/8/time");
    });
  });

  it("leaves data freshness to the top bar", () => {
    renderSidebar();
    expect(screen.queryByText("Data status")).toBeNull();
  });

  it("marks Routes active on a route dossier", () => {
    renderSidebar("/agencies/8/routes/50?routes=50");
    expect(screen.getByRole("link", { name: "Routes" }).getAttribute("aria-current")).toBe("page");
  });

  it("lists Help under Other, without Admin for a visitor who is not an admin", () => {
    renderSidebar();
    const other = screen.getByRole("navigation", { name: "Other" });
    expect(within(other).getByRole("link", { name: "Help" })).toHaveAttribute("href", "/help");
    expect(within(other).queryByRole("link", { name: "Admin" })).toBeNull();
  });

  it("links the welcome page under Other, for signed-in and signed-out visitors alike", () => {
    renderSidebar();
    const other = screen.getByRole("navigation", { name: "Other" });
    expect(within(other).getByRole("link", { name: "About this app" })).toHaveAttribute("href", "/welcome");
  });

  describe("with an admin session", () => {
    afterEach(() => vi.restoreAllMocks());

    it("lists Admin under Other", () => {
      vi.spyOn(config, "useConfig").mockReturnValue({ data: { auth_enabled: true }, isLoading: false } as never);
      vi.spyOn(auth, "useSession").mockReturnValue({
        data: { email: "admin@example.test", name: "", role: "admin" },
        isLoading: false,
      } as never);
      renderSidebar();
      const other = screen.getByRole("navigation", { name: "Other" });
      expect(within(other).getByRole("link", { name: "Admin" })).toHaveAttribute("href", "/admin");
    });
  });

  it("does not render Live outside any agency context", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nextProvider i18n={i18n}>
          <ToastProvider>
            <MemoryRouter initialEntries={["/"]}>
              <Sidebar />
            </MemoryRouter>
          </ToastProvider>
        </I18nextProvider>
      </QueryClientProvider>
    );
    expect(screen.queryByRole("link", { name: /Live/ })).toBeNull();
  });

  it("marks the current route's nav link as active", () => {
    renderSidebar("/agencies/1/live");
    const mapLink = screen.getByRole("link", { name: "Live" });
    expect(mapLink.getAttribute("aria-current")).toBe("page");
  });

  it("renders the brand as a one-line wordmark above the nav items, leaving the tagline to the sign-in pages", () => {
    renderSidebar();
    const wordmark = screen.getByText("Delay Dashboard");
    expect(wordmark.style.whiteSpace).toBe("nowrap");
    expect(wordmark.style.wordBreak).toBe("keep-all");
    expect(screen.queryByText("Real-time × Timetable")).toBeNull();
  });

  it("renders the brand block even when there is no agencyId, but not the nav items", () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <I18nextProvider i18n={i18n}>
          <ToastProvider>
            <MemoryRouter initialEntries={["/"]}>
              <Sidebar />
            </MemoryRouter>
          </ToastProvider>
        </I18nextProvider>
      </QueryClientProvider>
    );
    expect(screen.getByText("Delay Dashboard")).toBeTruthy();
    expect(screen.queryByText("Live")).toBeNull();
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
    renderSidebar("/agencies/8/live");
    const link = screen.getByRole("link", { name: "No-data state" });
    expect(link).toHaveAttribute("href", "/agencies/8/pulse?from=2030-01-01&to=2030-01-07");
  });

  it("points the feed-stale prototype link at the current agency's live view, preserving the active filter", () => {
    renderSidebar("/agencies/8/live?from=2026-06-01&to=2026-06-07");
    const link = screen.getByRole("link", { name: "Feed-stale state" });
    expect(link).toHaveAttribute("href", "/agencies/8/live?from=2026-06-01&to=2026-06-07");
  });

  describe("collapse", () => {
    beforeEach(() => localStorage.clear());

    it("hides nav labels/subtitles and the PROTOTYPE section, but keeps the nav links, after collapsing", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      expect(screen.queryByText("Live")).toBeNull();
      expect(screen.queryByText("What's happening right now")).toBeNull();
      expect(screen.queryByText("PROTOTYPE")).toBeNull();
      expect(screen.getByRole("link", { name: "Live" })).toBeTruthy();
    });

    it("shows an expand toggle once collapsed, which restores the labels when clicked", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
      expect(screen.getByText("Live")).toBeTruthy();
    });

    it("persists the collapsed state to localStorage and restores it on remount", () => {
      localStorage.setItem("transit.sidebarCollapsed", "1");
      renderSidebar();
      expect(screen.queryByText("Live")).toBeNull();
      expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeTruthy();
    });

    it("hides the account-menu trigger while collapsed", async () => {
      const user = userEvent.setup();
      renderSidebar();
      expect(await screen.findByRole("button", { name: "Account menu" })).toBeTruthy();
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      expect(screen.queryByRole("button", { name: "Account menu" })).toBeNull();
    });

    it("snaps between widths rather than animating layout", async () => {
      const user = userEvent.setup();
      const { container } = renderSidebar();
      const aside = container.querySelector<HTMLElement>(".app-sidebar-desktop")!;
      expect(aside.style.transition).not.toMatch(/width/);
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      expect(aside.style.width).toBe("76px");
      expect(aside.style.transition).not.toMatch(/width/);
    });
  });

  describe("station figures", () => {
    beforeEach(() => localStorage.clear());
    afterEach(() => vi.restoreAllMocks());

    function mockToday(routes: { route_code: string; avg_delay_sec: number; trips_observed: number; bucket: string }[]) {
      vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: { routes } } as never);
    }

    it("describes Pulse with today's mean and Live with the routes running later than usual, keeping their names", () => {
      mockToday([
        { route_code: "50", avg_delay_sec: 180, trips_observed: 10, bucket: "anomaly" },
        { route_code: "24", avg_delay_sec: 60, trips_observed: 30, bucket: "normal" },
        { route_code: "3", avg_delay_sec: 240, trips_observed: 10, bucket: "anomaly" },
      ]);
      renderSidebar("/agencies/8/time");
      expect(screen.getByRole("link", { name: "Pulse" })).toHaveAccessibleDescription("Mean 2.0 min");
      expect(screen.getByRole("link", { name: "Live" })).toHaveAccessibleDescription("2 unusually late");
      expect(screen.getByRole("link", { name: "Routes" })).not.toHaveAttribute("aria-describedby");
    });

    it("shows no figure without a summary, or for no route running late", () => {
      mockToday([{ route_code: "24", avg_delay_sec: 60, trips_observed: 0, bucket: "normal" }]);
      renderSidebar("/agencies/8/time");
      expect(screen.getByRole("link", { name: "Pulse" })).not.toHaveAttribute("aria-describedby");
      expect(screen.getByRole("link", { name: "Live" })).not.toHaveAttribute("aria-describedby");
    });

    it("drops the figures from the collapsed rail", async () => {
      const user = userEvent.setup();
      mockToday([{ route_code: "50", avg_delay_sec: 180, trips_observed: 10, bucket: "anomaly" }]);
      renderSidebar("/agencies/8/time");
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      expect(screen.getByRole("link", { name: "Live" })).not.toHaveAttribute("aria-describedby");
    });
  });

  describe("my routes", () => {
    beforeEach(() => localStorage.clear());
    afterEach(() => {
      localStorage.clear();
      vi.restoreAllMocks();
    });

    it("invites a pin while the list is empty", () => {
      renderSidebar("/agencies/8/live");
      const mine = screen.getByRole("navigation", { name: "My routes" });
      expect(within(mine).getByText("Pin a route on its page to open it from here.")).toBeTruthy();
    });

    it("opens a pinned route's page with the Routes screen's filters, beside today's trip-weighted mean", () => {
      togglePinnedRoute(8, "50");
      rememberScreenScope("8", "routes", "from=2026-06-01&to=2026-06-07");
      vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
        data: {
          routes: [
            { route_code: "50", avg_delay_sec: 120, trips_observed: 10 },
            { route_code: "50", avg_delay_sec: 240, trips_observed: 30 },
            { route_code: "24", avg_delay_sec: 600, trips_observed: 5 },
          ],
        },
      } as never);
      renderSidebar("/agencies/8/live");
      const mine = screen.getByRole("navigation", { name: "My routes" });
      expect(within(mine).getByRole("link", { name: "Route 50, today's mean 3.5 min" })).toHaveAttribute(
        "href",
        "/agencies/8/routes/50?from=2026-06-01&to=2026-06-07",
      );
    });

    it("names a pinned route alone when it has run no trip today", () => {
      togglePinnedRoute(8, "50");
      renderSidebar("/agencies/8/live");
      expect(within(screen.getByRole("navigation", { name: "My routes" })).getByRole("link", { name: "Route 50" })).toBeTruthy();
    });

    it("says where a pinned route goes beside its badge, and names it in full", () => {
      togglePinnedRoute(8, "50");
      vi.spyOn(hooks, "useRoutes").mockReturnValue({
        data: [{ route_id: "r50", route_short_name: "50", route_long_name: null, route_code: "50", trip_headsigns: ["Nakasuji"] }],
      } as never);
      renderSidebar("/agencies/8/live");
      const link = within(screen.getByRole("navigation", { name: "My routes" })).getByRole("link", { name: "50 · for Nakasuji" });
      expect(within(link).getByText("for Nakasuji")).toBeTruthy();
    });

    it("leaves the collapsed rail without the list while it is empty", async () => {
      const user = userEvent.setup();
      renderSidebar("/agencies/8/live");
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      expect(screen.queryByRole("navigation", { name: "My routes" })).toBeNull();
    });
  });

  describe("line map", () => {
    beforeEach(() => localStorage.clear());

    const stopNames = (group: HTMLElement) => within(group).getAllByRole("link").map((link) => link.textContent);

    it("opens the current station's stops below it, and no other station's", () => {
      renderSidebar("/agencies/8/routes");
      const stops = screen.getByRole("group", { name: "Routes views" });
      expect(stopNames(stops)).toEqual(["Delay ranking", "Lowest average delay", "On-time rate", "Delays over 5 min"]);
      expect(screen.queryByRole("group", { name: "Time views" })).toBeNull();
    });

    it("opens no stops on a screen with a single view", () => {
      renderSidebar("/agencies/8/why");
      expect(screen.queryByRole("group", { name: /views$/ })).toBeNull();
    });

    it.each([
      ["/agencies/8/routes?report=on_time", "Routes views", "On-time rate"],
      ["/agencies/8/routes?sort=worst_5min", "Routes views", "Delays over 5 min"],
      ["/agencies/8/time", "Time views", "Trend"],
      ["/agencies/8/compare?by=agencies", "Compare views", "Agencies"],
      ["/agencies/8/reports?doc=saved", "Reports views", "Saved analyses"],
    ])("on %s marks the stop the page shows", (path, group, current) => {
      renderSidebar(path);
      const stops = within(screen.getByRole("group", { name: group })).getAllByRole("link");
      expect(stops.filter((link) => link.getAttribute("aria-current") === "true").map((link) => link.textContent)).toEqual([current]);
    });

    it("marks no stop on a route dossier, which is none of Routes' reports", () => {
      renderSidebar("/agencies/8/routes/50?routes=50");
      const stops = within(screen.getByRole("group", { name: "Routes views" })).getAllByRole("link");
      expect(stops.every((link) => link.getAttribute("aria-current") === "false")).toBe(true);
    });

    it("opens a stop with its screen's own filters", () => {
      renderSidebar("/agencies/8/time?from=2026-06-01&to=2026-06-07&report=trend");
      expect(screen.getByRole("link", { name: "Route forecast" })).toHaveAttribute(
        "href",
        "/agencies/8/time?from=2026-06-01&to=2026-06-07&report=route_forecast",
      );
    });

    it("keeps the open station's stops as named links once collapsed", async () => {
      const user = userEvent.setup();
      renderSidebar("/agencies/8/compare");
      await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
      const stops = within(screen.getByRole("group", { name: "Compare views" })).getAllByRole("link");
      expect(stops.map((link) => link.getAttribute("aria-label"))).toEqual(["Weekdays and weekends", "Agencies"]);
    });

    it("names Ask by its label alone, leaving the transfer mark to sight", () => {
      renderSidebar();
      expect(screen.getByRole("link", { name: "Ask" })).toBeTruthy();
      expect(screen.getByText("Transfer")).toHaveAttribute("aria-hidden", "true");
    });

    it("shows the period of the screen on show beside the agency", () => {
      renderSidebar("/agencies/8/live?from=2026-06-01&to=2026-06-07&dow=weekend");
      expect(screen.getByText("Period")).toBeTruthy();
      expect(screen.getByText("6/1 – 6/7")).toBeTruthy();
      expect(screen.getByText("weekends")).toBeTruthy();
    });

    it("leaves the period off pages outside any agency", () => {
      renderSidebar("/help");
      expect(screen.queryByText("Period")).toBeNull();
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
      expect(screen.getAllByRole("link", { name: /Live/ }).length).toBe(1);
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

    it("renders Pulse, Routes, Live and Ask as tabs, leaving the rest to the More sheet", () => {
      renderSidebar();
      const nav = screen.getByRole("navigation", { name: "Primary navigation" });
      expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual(["Pulse", "Routes", "Live", "Ask"]);
    });

    it("marks the active tab", () => {
      renderSidebar("/agencies/1/live");
      const nav = screen.getByRole("navigation", { name: "Primary navigation" });
      expect(within(nav).getByRole("link", { name: /Live/ })).toHaveAttribute("aria-current", "page");
    });

    it("does not render the four destinations outside any agency context", () => {
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <I18nextProvider i18n={i18n}>
            <ToastProvider>
              <MemoryRouter initialEntries={["/"]}>
                <Sidebar />
              </MemoryRouter>
            </ToastProvider>
          </I18nextProvider>
        </QueryClientProvider>
      );
      expect(screen.queryByRole("link", { name: /Live/ })).toBeNull();
      expect(screen.getByRole("button", { name: "More" })).toBeTruthy();
    });

    it("sets the More label in the same type as the other tab labels", () => {
      renderSidebar();
      const more = screen.getByRole("button", { name: "More" });
      const pulse = screen.getByRole("link", { name: "Pulse" });
      expect(more.style.fontSize).toBe(pulse.style.fontSize);
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
      expect(within(dialog).queryByRole("link", { name: /Live/ })).toBeNull();
    });

    it("puts the account menu above the destinations, on the sheet's first screen", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "More" }));
      const dialog = screen.getByRole("dialog");
      const account = await within(dialog).findByRole("button", { name: "Account menu" });
      const nav = within(dialog).getByRole("navigation", { name: "Destinations" });
      expect(account.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it("keeps its close button pinned while the sheet scrolls", async () => {
      const user = userEvent.setup();
      renderSidebar();
      await user.click(screen.getByRole("button", { name: "More" }));
      const close = within(screen.getByRole("dialog")).getByRole("button", { name: "Close menu" });
      expect(close.parentElement?.style.position).toBe("sticky");
    });

    it("carries the destinations that have no tab of its own on a phone, and Help", async () => {
      const user = userEvent.setup();
      renderSidebar("/agencies/1/live?from=2026-06-01&to=2026-06-07");
      await user.click(screen.getByRole("button", { name: "More" }));
      const dialog = screen.getByRole("dialog");
      const nav = within(dialog).getByRole("navigation", { name: "Destinations" });
      expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual(["Time", "Why", "Compare", "Reports"]);
      expect(within(nav).getByRole("link", { name: "Reports" })).toHaveAttribute("href", "/agencies/1/reports");
      expect(within(dialog).getByRole("link", { name: "Help" })).toHaveAttribute("href", "/help");
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
