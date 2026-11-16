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

type Config = { auth_enabled: boolean; local_admin_enabled: boolean; login_required?: boolean };
const mockUseConfig = vi.fn<() => { data: Config | undefined }>();
vi.mock("../api/config", () => ({ useConfig: () => mockUseConfig() }));
const OPEN: Config = { auth_enabled: true, local_admin_enabled: false, login_required: false };
const GATED: Config = { auth_enabled: true, local_admin_enabled: false, login_required: true };

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
    mockUseConfig.mockReturnValue({ data: OPEN });
    clearLastAgency();
  });

  describe("the one CTA decision, shared by the header button and the hero's primary CTA", () => {
    it("opens the dashboard without sign-in when the deployment needs none, and says what sign-in adds", () => {
      renderLanding();
      const ctas = screen.getAllByRole("link", { name: "View the dashboard (no sign-in needed)" });
      expect(ctas).toHaveLength(2);
      for (const cta of ctas) expect(cta).toHaveAttribute("href", "/");
      const note = screen.getByText(/to keep your notebook investigations/);
      expect(within(note).getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    });

    it("stays open to a signed-in visitor and offers the dashboard in place of sign-in", () => {
      mockUseSession.mockReturnValue({ data: { user_id: 1 }, isLoading: false });
      renderLanding();
      const ctas = screen.getAllByRole("link", { name: "Open the dashboard" });
      expect(ctas).toHaveLength(2);
      for (const cta of ctas) expect(cta).toHaveAttribute("href", "/");
      expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
      expect(screen.queryByText(/to keep your notebook investigations/)).toBeNull();
    });

    it("offers sign-in as the way in when the deployment requires it", () => {
      mockUseConfig.mockReturnValue({ data: GATED });
      renderLanding();
      const ctas = screen.getAllByRole("link", { name: "Sign in" });
      expect(ctas).toHaveLength(2);
      for (const cta of ctas) expect(cta).toHaveAttribute("href", "/login");
      expect(screen.queryByRole("link", { name: /no sign-in needed/ })).toBeNull();
      expect(screen.queryByText(/to keep your notebook investigations/)).toBeNull();
    });

    it("leaves out the sign-in note on a deployment with nothing to sign in with", () => {
      mockUseConfig.mockReturnValue({ data: { ...OPEN, auth_enabled: false } });
      renderLanding();
      const ctas = screen.getAllByRole("link", { name: "View the dashboard (no sign-in needed)" });
      expect(ctas).toHaveLength(2);
      for (const cta of ctas) expect(cta).toHaveAttribute("href", "/");
      expect(screen.queryByText(/to keep your notebook investigations/)).toBeNull();
    });

    it("offers sign-in, not the guest entry, while the session is still loading", () => {
      mockUseSession.mockReturnValue({ data: undefined, isLoading: true });
      renderLanding();
      const ctas = screen.getAllByRole("link", { name: "Sign in" });
      expect(ctas).toHaveLength(2);
      for (const cta of ctas) expect(cta).toHaveAttribute("href", "/login");
      expect(screen.queryByRole("link", { name: /no sign-in needed/ })).toBeNull();
      expect(screen.queryByText(/to keep your notebook investigations/)).toBeNull();
    });

    it("promises no sign-in-free entry before the configuration says so", () => {
      mockUseConfig.mockReturnValue({ data: undefined });
      renderLanding();
      const ctas = screen.getAllByRole("link", { name: "Sign in" });
      expect(ctas).toHaveLength(2);
      for (const cta of ctas) expect(cta).toHaveAttribute("href", "/login");
      expect(screen.queryByRole("link", { name: /no sign-in needed/ })).toBeNull();
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
    it("sends a visitor who reaches the app straight to their last-chosen agency's Bus Town", () => {
      writeLastAgency(7);
      renderLanding();
      expect(screen.getByRole("link", { name: "Open Bus Town" })).toHaveAttribute("href", "/agencies/7/bus-town");
    });

    it("sends a visitor who reaches the app but has no stored agency to the app root to choose one", () => {
      renderLanding();
      expect(screen.getByRole("link", { name: "Open Bus Town" })).toHaveAttribute("href", "/");
    });

    it("sends a visitor who would not reach the app to sign-in first, regardless of a stored agency", () => {
      writeLastAgency(7);
      mockUseConfig.mockReturnValue({ data: GATED });
      renderLanding();
      expect(screen.getByRole("link", { name: "Open Bus Town" })).toHaveAttribute("href", "/login");
    });

    it("sends a visitor whose session is still loading to sign-in first, regardless of a stored agency", () => {
      writeLastAgency(7);
      mockUseSession.mockReturnValue({ data: undefined, isLoading: true });
      renderLanding();
      expect(screen.getByRole("link", { name: "Open Bus Town" })).toHaveAttribute("href", "/login");
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
