import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import i18n from "../i18n";
import { writeLastAgency, clearLastAgency } from "../api/lastAgency";
import { LandingPage } from "./LandingPage";

void i18n.changeLanguage("en");

const mockUseSession = vi.fn<() => { data: unknown; isLoading: boolean }>(() => ({ data: null, isLoading: false }));
vi.mock("../api/auth", () => ({ useSession: () => mockUseSession() }));


function renderLanding() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <MemoryRouter initialEntries={["/welcome"]}>
          <Routes>
            <Route path="/welcome" element={<LandingPage />} />
          </Routes>
        </MemoryRouter>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("LandingPage", () => {
  beforeEach(() => {
    mockUseSession.mockReturnValue({ data: null, isLoading: false });
    clearLastAgency();
  });

  describe("the one CTA decision, shared by the header button and the hero's primary CTA", () => {
    it("opens the dashboard to a signed-out visitor without sign-in, and asks nothing of them", () => {
      renderLanding();
      const ctas = screen.getAllByRole("link", { name: "View the dashboard (no sign-in needed)" });
      expect(ctas).toHaveLength(2);
      for (const cta of ctas) expect(cta).toHaveAttribute("href", "/");
      expect(screen.queryByRole("link", { name: /sign in/i })).toBeNull();
    });

    it("stays open to a signed-in visitor and offers to open the dashboard", () => {
      mockUseSession.mockReturnValue({ data: { user_id: 1 }, isLoading: false });
      renderLanding();
      const ctas = screen.getAllByRole("link", { name: "Open the dashboard" });
      expect(ctas).toHaveLength(2);
      for (const cta of ctas) expect(cta).toHaveAttribute("href", "/");
    });

    it("opens the dashboard while the session is still loading", () => {
      mockUseSession.mockReturnValue({ data: undefined, isLoading: true });
      renderLanding();
      const ctas = screen.getAllByRole("link", { name: "View the dashboard (no sign-in needed)" });
      expect(ctas).toHaveLength(2);
      for (const cta of ctas) expect(cta).toHaveAttribute("href", "/");
    });
  });

  describe("the pause toggle", () => {
    it("starts unpressed, and pauses every animation (via the shared root class) once clicked", () => {
      const { container } = renderLanding();
      const toggle = screen.getByRole("button", { name: "Pause animations" });
      expect(toggle).toHaveAttribute("aria-pressed", "false");
      expect(container.querySelector(".landing-shell")).not.toHaveClass("is-paused");

      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-pressed", "true");
      expect(container.querySelector(".landing-shell")).toHaveClass("is-paused");
      // The accessible name does not change with state -- only aria-pressed does.
      expect(screen.getByRole("button", { name: "Pause animations" })).toBe(toggle);

      fireEvent.click(toggle);
      expect(toggle).toHaveAttribute("aria-pressed", "false");
      expect(container.querySelector(".landing-shell")).not.toHaveClass("is-paused");
    });
  });

  describe("the Bus Town teaser's one real CTA", () => {
    it("sends a visitor straight to their last-chosen agency's Bus Town", () => {
      writeLastAgency(7);
      renderLanding();
      expect(screen.getByRole("link", { name: "Open Bus Town" })).toHaveAttribute("href", "/agencies/7/bus-town");
    });

    it("sends a visitor with no stored agency to the app root to choose one", () => {
      renderLanding();
      expect(screen.getByRole("link", { name: "Open Bus Town" })).toHaveAttribute("href", "/");
    });

    it("sends a visitor whose session is still loading to Bus Town too", () => {
      writeLastAgency(7);
      mockUseSession.mockReturnValue({ data: undefined, isLoading: true });
      renderLanding();
      expect(screen.getByRole("link", { name: "Open Bus Town" })).toHaveAttribute("href", "/agencies/7/bus-town");
    });
  });

  it("labels every section built from a fixture as sample data, never a live read", () => {
    const { container } = renderLanding();
    const status = screen.getByLabelText("Current observation status");
    expect(within(status).getByText("Sample data")).toBeTruthy();
    for (const id of ["observatory", "method", "bus-town"]) {
      const section = container.querySelector(`#${id}`);
      expect(section, id).toBeTruthy();
      expect(within(section as HTMLElement).getByText("Sample data"), id).toBeTruthy();
    }
    // The council report figure carries its own "Sample" badge instead.
    const councils = container.querySelector("#councils");
    expect(councils, "councils").toBeTruthy();
    expect(within(councils as HTMLElement).getByText("Sample", { selector: ".landing-report__badge" })).toBeTruthy();
  });

  it("every in-page nav/door anchor points at a section id that actually exists on the page", () => {
    const { container } = renderLanding();
    const anchors = container.querySelectorAll<HTMLAnchorElement>('a[href^="#"]');
    expect(anchors.length).toBeGreaterThan(0);
    for (const a of anchors) {
      const id = a.getAttribute("href")!.slice(1);
      expect(container.querySelector(`#${id}`), `no element with id="${id}" for anchor "${a.textContent}"`).toBeTruthy();
    }
  });

  it("links to the terms of service and privacy policy", () => {
    renderLanding();
    expect(screen.getByRole("link", { name: "Terms of Service" })).toHaveAttribute("href", "/terms");
    expect(screen.getByRole("link", { name: "Privacy Policy" })).toHaveAttribute("href", "/privacy");
  });

  it("credits the open-data sources in its footer", () => {
    renderLanding();
    const credits = within(screen.getByRole("contentinfo")).getByRole("region", { name: "Data sources" });
    expect(within(credits).getByRole("link", { name: "Aomori City Bus information" })).toHaveAttribute(
      "href",
      "https://ckan.odpt.org/dataset/aomori_city_all_lines",
    );
    expect(within(credits).getByRole("link", { name: "Creative Commons CC0" })).toHaveAttribute(
      "href",
      "https://creativecommons.org/publicdomain/zero/1.0/deed.en",
    );
  });

  afterEach(() => {
    clearLastAgency();
  });
});
