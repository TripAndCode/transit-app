import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { I18nextProvider } from "react-i18next";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import i18n from "../i18n";
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
  });

  it("says in plain words what the app covers and what it answers", () => {
    renderLanding();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "What's happening right now — and where it's happening.",
    );
    expect(screen.getByText("Buses in Hiroshima and Aomori")).toBeTruthy();
    expect(
      screen.getByText(
        "See which trips are running late right now and by how much, and which routes and times of day tend to run late.",
      ),
    ).toBeTruthy();
  });

  it("says the same in Japanese, without the internal terms", async () => {
    try {
      await i18n.changeLanguage("ja");
      const { container } = renderLanding();
      expect(screen.getByText("広島・青森のバス")).toBeTruthy();
      expect(screen.getByText("いまどの便が何分遅れているか、どの路線・時間帯で遅れやすいかを確認できます。")).toBeTruthy();
      expect(screen.getByRole("link", { name: "ダッシュボードを見る（ログイン不要）" })).toHaveAttribute("href", "/");
      const hero = container.querySelector(".landing-hero__content")!;
      expect(hero.textContent).not.toMatch(/最新報告停留所|リアルタイム × 時刻表/);
      expect(hero.textContent).toContain("ログインすると表示条件の保存と質問の履歴が使えます");
    } finally {
      await i18n.changeLanguage("en");
    }
  });

  it("opens the dashboard without sign-in when the deployment needs none, and says what sign-in adds", () => {
    renderLanding();
    expect(screen.getByRole("link", { name: "View the dashboard (no sign-in needed)" })).toHaveAttribute("href", "/");
    const note = screen.getByText(/to save your view settings/);
    expect(note).toHaveTextContent("Sign in to save your view settings and keep your question history.");
    expect(within(note).getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("leaves out the sign-in note on a deployment with nothing to sign in with", () => {
    mockUseConfig.mockReturnValue({ data: { ...OPEN, auth_enabled: false } });
    renderLanding();
    expect(screen.getByRole("link", { name: "View the dashboard (no sign-in needed)" })).toBeTruthy();
    expect(screen.queryByText(/to save your view settings/)).toBeNull();
  });

  it("offers sign-in as the way in when the deployment requires it", () => {
    mockUseConfig.mockReturnValue({ data: GATED });
    renderLanding();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("link", { name: /no sign-in needed/ })).toBeNull();
  });

  it("offers sign-in, not the guest entry, while the session is still loading", () => {
    mockUseSession.mockReturnValue({ data: undefined, isLoading: true });
    renderLanding();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("link", { name: /no sign-in needed/ })).toBeNull();
    expect(screen.queryByText(/to save your view settings/)).toBeNull();
  });

  it("promises no sign-in-free entry before the configuration says so", () => {
    mockUseConfig.mockReturnValue({ data: undefined });
    renderLanding();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("link", { name: /no sign-in needed/ })).toBeNull();
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

  it("stays open to a signed-in visitor and offers the dashboard in place of sign-in", () => {
    mockUseSession.mockReturnValue({ data: { user_id: 1 }, isLoading: false });
    renderLanding();
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open the dashboard" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
    expect(screen.queryByText(/to save your view settings/)).toBeNull();
  });

  it("renders the scroll narrative's real chart sections below the hero, not the retired DashboardPreview mock", () => {
    renderLanding();
    // The three narrative section headings.
    expect(screen.getByRole("heading", { name: "Delay builds along a route" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Every day, compared" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Ask, get evidence" })).toBeTruthy();
    // Real components, not a mocked shell -- see ScrollNarrative.test.tsx for
    // the full per-section assertions.
    expect(screen.getByRole("group", { name: "Delay by stop" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Daily average delay chart" })).toBeTruthy();
  });
});
